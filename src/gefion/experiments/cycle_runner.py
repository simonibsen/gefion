"""Autonomous experiment cycle orchestrator.

Chains discovery → propose → approve → run → evaluate into a single
autonomous workflow with configurable guardrails.
"""
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional

import psycopg

from gefion.experiments.core import ExperimentConfig, ExperimentRunner
from gefion.experiments.discovery import run_discovery
from gefion.experiments.principles import load_principles
from gefion.experiments.safety import run_preflight_checks
from gefion.experiments.statistical import apply_fdr, compute_holdout_pvalue
from gefion.observability import create_span, set_attributes, add_event

logger = logging.getLogger(__name__)


# Sensible defaults when the user doesn't specify search bounds.
DEFAULT_SEARCH_SPACES = {
    "hyperparameter": {
        "learning_rate": {"type": "float", "low": 0.005, "high": 0.3, "log": True},
        "n_estimators": {"type": "int", "low": 50, "high": 500},
        "max_depth": {"type": "int", "low": 2, "high": 12},
    },
    "model_comparison": {
        "model_type": ["lightgbm", "xgboost", "quantile_regression"],
    },
    "feature_selection": {
        # Built dynamically from available features
    },
    "feature_engineering": {
        "window": {"type": "int", "low": 5, "high": 30, "step": 5},
    },
    "label_engineering": {
        "label_type": ["raw", "log_return", "winsorized", "sign"],
    },
    "strategy_params": {
        "lookback_days": {"type": "int", "low": 5, "high": 30},
    },
}


class CycleRunner:
    """Orchestrates a full autonomous experiment cycle.

    Usage:
        runner = CycleRunner("postgresql://...")
        results = runner.run_cycle(cycle_id)
    """

    def __init__(self, db_url: str):
        self.db_url = db_url
        self.runner = ExperimentRunner(db_url)

    def run_cycle(self, cycle_id: int, on_progress: Optional[callable] = None) -> Dict[str, Any]:
        """Execute a full autonomous experiment cycle.

        Steps:
            1. Load cycle config (guardrails, allowed types, search bounds)
            2. Run discovery to find testable hypotheses
            3. Filter by allowed types, propose experiments
            4. Auto-approve if configured
            5. Run experiments (parallel with resource checks)
            6. Evaluate results with FDR correction
            7. Mark survivors and update cycle status

        Args:
            cycle_id: ID of the cycle to run (from experiment_cycles table).
            on_progress: Optional callback(phase, message, detail) for status updates.

        Returns:
            Dict with proposed, completed, fdr_survivors counts and results.
        """
        def _emit(phase, message, detail=None):
            if on_progress:
                on_progress(phase, message, detail)

        with create_span("experiments.cycle_runner.run_cycle", cycle_id=cycle_id) as span:
            # 1. Load cycle
            _emit("loading", "Loading cycle configuration...")
            cycle = self._load_cycle(cycle_id)
            config = cycle.get("config", {})
            allowed_types = config.get("allowed_types", list(DEFAULT_SEARCH_SPACES.keys()))
            auto_approve = config.get("auto_approve", True)
            max_experiments = cycle.get("max_experiments", 20)
            max_parallel = config.get("max_parallel", 3)
            max_trials = config.get("max_trials_per_experiment") or 10
            search_method = config.get("search_method") or "bayesian"
            dataset_uri = config.get("dataset_uri")
            horizon_days = config.get("horizon_days", 7)
            algorithm = config.get("algorithm", "lightgbm")
            search_bounds = config.get("search_bounds", {})

            allowed_algorithms = config.get("allowed_algorithms", ["lightgbm", "xgboost", "quantile_regression"])
            allowed_horizons = config.get("allowed_horizons", [7, 30])
            quantiles = config.get("quantiles")  # None = agent decides
            cv_folds = config.get("cv_folds")  # None = agent decides
            embargo_pct = config.get("embargo_pct")  # None = agent decides

            # Preflight: validate dataset before proposing experiments
            _emit("preflight", "Validating dataset and data availability...")
            preflight_issues = self._preflight_check(
                dataset_uri=dataset_uri,
                allowed_horizons=allowed_horizons,
                allowed_types=allowed_types,
            )
            if preflight_issues:
                for issue in preflight_issues:
                    _emit("preflight_warning", issue["message"], issue)

                # Filter out experiment types that can't run
                blocked_types = {i["blocks_type"] for i in preflight_issues if i.get("blocks_type")}
                if blocked_types:
                    before = len(allowed_types)
                    allowed_types = [t for t in allowed_types if t not in blocked_types]
                    _emit("preflight",
                          f"Removed {before - len(allowed_types)} experiment type(s) that can't run with available data")

                if not allowed_types:
                    _emit("preflight", "No experiment types can run with available data. Fix the issues above first.")
                    self._update_cycle_status(cycle_id, "failed", {
                        "proposed": 0, "completed": 0, "failed": 0, "fdr_survivors": 0,
                        "errors": [i["message"] for i in preflight_issues],
                    })
                    return {
                        "proposed": 0, "completed": 0, "failed": 0, "fdr_survivors": 0,
                        "errors": [i["message"] for i in preflight_issues],
                    }

            set_attributes(span,
                           max_experiments=max_experiments,
                           allowed_types=",".join(allowed_types),
                           auto_approve=auto_approve)

            # 2. Discovery
            _emit("discovery", "Running data discovery...",
                  {"themes": config.get("selected_themes")})
            selected_themes = config.get("selected_themes")
            hypotheses = self._run_discovery(selected_themes=selected_themes)
            ready = [
                h for h in hypotheses
                if h.get("feasibility") == "ready"
                and h.get("experiment_type") in allowed_types
            ]
            set_attributes(span, total_hypotheses=len(hypotheses), ready_hypotheses=len(ready))
            _emit("discovery", f"Found {len(hypotheses)} hypotheses, {len(ready)} ready",
                  {"total": len(hypotheses), "ready": len(ready)})

            # 3. Propose experiments (up to max)
            _emit("proposing", f"Proposing up to {min(len(ready), max_experiments)} experiments...")
            proposed_ids = []
            for h in ready[:max_experiments]:
                # Build search space: user bounds override defaults
                exp_type = h["experiment_type"]
                search_space = dict(DEFAULT_SEARCH_SPACES.get(exp_type, {}))
                if exp_type in search_bounds:
                    search_space.update(search_bounds[exp_type])

                # For model_comparison, use allowed_algorithms
                if exp_type == "model_comparison" and allowed_algorithms:
                    search_space["model_type"] = allowed_algorithms

                # Build CV config with agent-decidable settings
                cv_config = {}
                if cv_folds is not None:
                    cv_config["n_splits"] = cv_folds
                else:
                    cv_config["n_splits"] = 5  # agent default
                if embargo_pct is not None:
                    cv_config["embargo_pct"] = embargo_pct
                else:
                    cv_config["embargo_pct"] = 0.02  # agent default

                # Pick horizon — if agent decides and multiple available, use first
                exp_horizon = horizon_days if horizon_days else allowed_horizons[0]

                # Pick algorithm — for non-comparison types, use first allowed
                exp_algorithm = algorithm if algorithm else allowed_algorithms[0]

                exp_config = {
                    "experiment_type": exp_type,
                    "principle_id": h.get("principle_id", ""),
                    "description": h.get("description", ""),
                    "search_space": search_space,
                    "dataset_uri": dataset_uri,
                    "horizon_days": exp_horizon,
                    "algorithm": exp_algorithm,
                    "quantiles": quantiles or [0.1, 0.5, 0.9],
                    "cv_config": cv_config,
                    "max_trials": max_trials,
                    "search_method": search_method,
                }
                exp_id = self._propose_experiment(exp_config, cycle_id)
                proposed_ids.append(exp_id)
                add_event(span, "proposed", experiment_id=exp_id, type=exp_type)
                _emit("proposed", f"Proposed experiment #{exp_id}: {exp_type}",
                      {"experiment_id": exp_id, "type": exp_type,
                       "principle": h.get("principle_id", "")})

            # 4. Auto-approve
            _emit("approving", f"{'Auto-approving' if auto_approve else 'Awaiting approval for'} {len(proposed_ids)} experiments...")
            if auto_approve:
                for exp_id in proposed_ids:
                    try:
                        self.runner.approve(exp_id, approver="cycle_runner")
                    except Exception as e:
                        logger.warning(f"Failed to approve experiment {exp_id}: {e}")

            # 5. Run experiments (parallel with safety checks)
            _emit("running", f"Running {len(proposed_ids)} experiments (max {max_parallel} parallel)...")
            results = self._run_experiments(proposed_ids, max_parallel, on_progress=_emit)
            set_attributes(span, completed=len(results))

            # 6. Evaluate with FDR
            _emit("evaluating", "Applying FDR correction to filter false discoveries...")
            fdr_results = self._evaluate_cycle(cycle_id, proposed_ids)

            # 7. Summarize and surface errors clearly
            successful = [r for r in results if r.get("status") == "completed" and "error" not in r]
            failed = [r for r in results if r.get("status") == "failed" or "error" in r]

            if failed:
                error_msgs = set(r.get("error", "unknown") for r in failed)
                _emit("errors",
                      f"{len(failed)} experiment(s) failed: {'; '.join(error_msgs)}",
                      {"failed_count": len(failed), "errors": list(error_msgs)})

            if not successful and failed:
                _emit("complete",
                      f"Cycle finished but all {len(failed)} experiments failed. "
                      f"Check dataset and data availability.",
                      {"all_failed": True})
            else:
                _emit("complete",
                      f"Cycle complete: {len(successful)} succeeded, {len(failed)} failed, "
                      f"{fdr_results.get('survivors', 0)} FDR survivors")

            summary = {
                "proposed": len(proposed_ids),
                "completed": len(successful),
                "failed": len(failed),
                "fdr_survivors": fdr_results.get("survivors", 0),
                "results": results,
                "errors": [r.get("error") for r in failed if r.get("error")],
            }
            self._update_cycle_status(cycle_id, "completed", summary)

            return summary

    def _preflight_check(
        self,
        dataset_uri: Optional[str],
        allowed_horizons: List[int],
        allowed_types: List[str],
    ) -> List[Dict[str, Any]]:
        """Validate that experiments can actually run with available data.

        Returns list of issues, each with 'message' and optional 'blocks_type'.
        Empty list = all clear.
        """
        issues = []

        # Check dataset exists and has required files
        ml_types = {"hyperparameter", "model_comparison", "feature_engineering",
                    "feature_selection", "label_engineering", "pipeline"}
        needs_dataset = bool(ml_types & set(allowed_types))

        if needs_dataset:
            from pathlib import Path

            if not dataset_uri:
                # Try auto-detect
                manifests = list(Path("datasets").glob("*/manifest.json")) if Path("datasets").exists() else []
                if manifests:
                    manifests.sort(key=lambda p: p.stat().st_mtime)
                    dataset_uri = str(manifests[-1])
                else:
                    issues.append({
                        "message": "No dataset found. Run 'gefion ml dataset-build' first.",
                        "severity": "critical",
                    })
                    for t in ml_types:
                        if t in allowed_types:
                            issues.append({"message": f"Cannot run {t} experiments without a dataset", "blocks_type": t})
                    return issues

            manifest_path = Path(dataset_uri)
            if not manifest_path.exists():
                issues.append({
                    "message": f"Dataset not found: {dataset_uri}",
                    "severity": "critical",
                })
                for t in ml_types:
                    if t in allowed_types:
                        issues.append({"message": f"Cannot run {t} without dataset", "blocks_type": t})
                return issues

            # Check for features and labels files
            dataset_dir = manifest_path.parent
            has_features = (dataset_dir / "features.parquet").exists() or (dataset_dir / "features.csv").exists()
            has_labels = (dataset_dir / "labels.parquet").exists() or (dataset_dir / "labels.csv").exists()

            if not has_features:
                issues.append({
                    "message": f"Dataset {dataset_dir.name} has no features file. Rebuild with 'gefion ml dataset-build'.",
                    "severity": "critical",
                })
            if not has_labels:
                issues.append({
                    "message": f"Dataset {dataset_dir.name} has no labels file. Rebuild with 'gefion ml dataset-build'.",
                    "severity": "critical",
                })

            # Check that requested horizons exist in the dataset
            if has_labels:
                try:
                    import json as _json
                    manifest = _json.loads(manifest_path.read_text())
                    available_horizons = manifest.get("horizons_days", [])
                    for h in allowed_horizons:
                        if available_horizons and h not in available_horizons:
                            issues.append({
                                "message": f"Horizon {h} days not in dataset (available: {available_horizons}). "
                                           f"Rebuild dataset with '--horizons {','.join(str(x) for x in allowed_horizons)}'.",
                                "severity": "warning",
                            })
                except Exception:
                    pass

            # Validate labels actually have data for the horizons
            if has_labels:
                try:
                    import pandas as pd
                    labels_file = dataset_dir / "labels.parquet"
                    if not labels_file.exists():
                        labels_file = dataset_dir / "labels.csv"
                    if labels_file.suffix == ".parquet":
                        labels_df = pd.read_parquet(labels_file)
                    else:
                        labels_df = pd.read_csv(labels_file)

                    for h in allowed_horizons:
                        horizon_labels = labels_df[labels_df["horizon_days"] == h]
                        if len(horizon_labels) == 0:
                            issues.append({
                                "message": f"No labels for horizon {h} days in dataset. "
                                           f"Rebuild with 'gefion ml dataset-build --horizons {h}'.",
                                "severity": "critical",
                            })
                            for t in ml_types:
                                if t in allowed_types:
                                    issues.append({"message": f"{t} blocked: no labels for horizon {h}", "blocks_type": t})
                except Exception as e:
                    issues.append({
                        "message": f"Could not validate labels: {e}",
                        "severity": "warning",
                    })

        # Check system resources
        try:
            checks = run_preflight_checks()
            if not checks.get("ok", True):
                for check in checks.get("checks", []):
                    if not check.get("ok", True):
                        issues.append({
                            "message": f"Resource warning: {check.get('message', 'unknown')}",
                            "severity": "warning",
                        })
        except Exception:
            pass

        return issues

    def _load_cycle(self, cycle_id: int) -> Dict[str, Any]:
        """Load cycle record from database.

        The cycle config (guardrails) is stored in the discovery_snapshot
        JSONB column under the "cycle_config" key.
        """
        with psycopg.connect(self.db_url) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT id, name, holdout_start_date, holdout_end_date,
                           fdr_rate, max_experiments, compute_budget_seconds,
                           status, discovery_snapshot
                    FROM experiment_cycles
                    WHERE id = %s
                """, (cycle_id,))
                row = cur.fetchone()
                if not row:
                    raise ValueError(f"Cycle {cycle_id} not found")

                snapshot = row[8] if row[8] else {}
                config = snapshot.get("cycle_config", {})

                return {
                    "id": row[0],
                    "name": row[1],
                    "holdout_start_date": row[2],
                    "holdout_end_date": row[3],
                    "fdr_rate": float(row[4]) if row[4] else 0.10,
                    "max_experiments": row[5] or 20,
                    "compute_budget_seconds": row[6] or 7200,
                    "status": row[7],
                    "config": config,
                }

    def _run_discovery(self, selected_themes: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """Run data discovery and return hypotheses, optionally filtered by themes."""
        with psycopg.connect(self.db_url) as conn:
            principles = load_principles()

            # Filter principles by selected themes
            if selected_themes:
                from gefion.ui.views.experiments import _get_theme_map
                theme_map = _get_theme_map()
                filtered = []
                for p in principles:
                    book = p.get("source", {}).get("title", "Other")
                    theme = theme_map.get(book, "Other")
                    if theme in selected_themes:
                        filtered.append(p)
                principles = filtered

            result = run_discovery(conn, principles)
            return result.get("hypotheses", [])

    def _propose_experiment(self, exp_config: Dict[str, Any], cycle_id: int) -> int:
        """Propose an experiment linked to the cycle."""
        config = ExperimentConfig(
            name=f"cycle-{cycle_id}-{exp_config['experiment_type']}-{exp_config.get('principle_id', 'auto')}",
            experiment_type=exp_config["experiment_type"],
            search_space=exp_config["search_space"],
            objective_metric="quantile_loss",
            objective_direction="minimize",
            max_trials=exp_config.get("max_trials", 10),
            search_method=exp_config.get("search_method", "bayesian"),
            principle_id=exp_config.get("principle_id"),
            null_hypothesis=exp_config.get("description"),
            extra_config={
                k: v for k, v in exp_config.items()
                if k not in ("experiment_type", "search_space", "max_trials", "search_method",
                             "principle_id", "description")
            },
        )
        exp_id = self.runner.propose(config, proposed_by="cycle_runner")

        # Link to cycle
        with psycopg.connect(self.db_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE experiments SET cycle_id = %s WHERE id = %s",
                    (cycle_id, exp_id),
                )
            conn.commit()

        return exp_id

    def _run_experiments(self, experiment_ids: List[int], max_parallel: int = 3,
                         on_progress: Optional[callable] = None) -> List[Dict[str, Any]]:
        """Run experiments sequentially with resource checks.

        Note: parallel execution via ThreadPoolExecutor was removed because
        ExperimentRunner.run() opens multiple DB connections per trial, and
        parallel experiments exhaust the connection pool causing deadlocks.
        Sequential execution is reliable and still fast enough for typical cycles.
        """
        results = []

        for i, exp_id in enumerate(experiment_ids, 1):
            # Safety check before each experiment
            try:
                checks = run_preflight_checks()
                if not checks.get("ok", True):
                    logger.warning(f"Preflight failed for experiment {exp_id}: {checks}")
                    if on_progress:
                        on_progress("experiment_failed",
                                    f"Experiment #{exp_id} skipped: resource check failed",
                                    {"experiment_id": exp_id})
                    results.append({"experiment_id": exp_id, "status": "skipped", "reason": "resource_check_failed"})
                    continue
            except Exception:
                pass  # Don't block on safety check failures

            try:
                if on_progress:
                    on_progress("running", f"Running experiment #{exp_id} ({i}/{len(experiment_ids)})...",
                               {"experiment_id": exp_id, "index": i, "total": len(experiment_ids)})
                result = self.runner.run(exp_id)
                score = result.get("best_score", "N/A")
                if on_progress:
                    on_progress("experiment_done",
                                f"Experiment #{exp_id} done ({i}/{len(experiment_ids)}), score: {score}",
                                {"experiment_id": exp_id, "score": score})
                results.append({"experiment_id": exp_id, "status": "completed", **result})
            except Exception as e:
                logger.error(f"Experiment {exp_id} failed: {e}")
                if on_progress:
                    on_progress("experiment_failed",
                                f"Experiment #{exp_id} failed ({i}/{len(experiment_ids)}): {e}",
                                {"experiment_id": exp_id, "error": str(e)})
                results.append({"experiment_id": exp_id, "status": "failed", "error": str(e)})

        return results

    def _evaluate_cycle(self, cycle_id: int, experiment_ids: List[int]) -> Dict[str, Any]:
        """Collect results and apply FDR correction."""
        # Load cycle FDR rate
        cycle = self._load_cycle(cycle_id)
        fdr_rate = cycle.get("fdr_rate", 0.10)

        # Collect best scores as proxy p-values
        # (In a full implementation, these would be holdout p-values from
        # compute_holdout_pvalue, but we use best_score as a simple proxy)
        scores = []
        valid_ids = []
        for exp_id in experiment_ids:
            try:
                exp = self.runner.get(exp_id)
                if exp and exp.get("status") == "completed" and exp.get("best_score") is not None:
                    scores.append(float(exp["best_score"]))
                    valid_ids.append(exp_id)
            except Exception:
                continue

        if not scores:
            return {"survivors": 0, "total": len(experiment_ids)}

        # Apply FDR (using scores as p-value proxy — lower is better for minimize)
        survivors_mask = apply_fdr(scores, fdr_rate)
        survivors = sum(survivors_mask)

        # Mark survivors in DB
        with psycopg.connect(self.db_url) as conn:
            with conn.cursor() as cur:
                for exp_id, survived in zip(valid_ids, survivors_mask):
                    cur.execute(
                        "UPDATE experiments SET fdr_survived = %s WHERE id = %s",
                        (survived, exp_id),
                    )
            conn.commit()

        return {
            "survivors": survivors,
            "total": len(valid_ids),
            "fdr_rate": fdr_rate,
            "survivor_ids": [eid for eid, s in zip(valid_ids, survivors_mask) if s],
        }

    def _update_cycle_status(self, cycle_id: int, status: str, summary: Dict[str, Any]) -> None:
        """Update cycle status and summary in database."""
        from psycopg.types.json import Json
        with psycopg.connect(self.db_url) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE experiment_cycles
                    SET status = %s, summary = %s, completed_at = NOW()
                    WHERE id = %s
                """, (status, Json(summary), cycle_id))
            conn.commit()
