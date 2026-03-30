"""
Core experiment abstractions.

Provides ExperimentConfig for defining experiments and ExperimentRunner
for managing experiment lifecycle (propose, approve, reject, run).
"""
import hashlib
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Dict, Any, Optional, List
import json
import psycopg

from gefion.observability import create_span, set_attributes

logger = logging.getLogger(__name__)

# Risk classification by experiment type
_RISK_LEVELS = {
    "feature_engineering": "medium",
    "feature_selection": "low",
    "hyperparameter": "low",
    "model_comparison": "low",
    "label_engineering": "high",
    "strategy_params": "low",
    "pipeline": "high",
}


def classify_risk_level(experiment_type: str) -> str:
    """Classify experiment risk level based on type.

    Returns 'low', 'medium', or 'high'.
    """
    return _RISK_LEVELS.get(experiment_type, "medium")


def is_duplicate_experiment(
    experiment_type: str,
    search_space: Dict[str, Any],
    principle_id: Optional[str],
    existing_experiments: List[Dict[str, Any]],
) -> bool:
    """Detect if an experiment duplicates an existing one.

    Compares experiment type + search space + principle_id hash.
    """
    def _config_hash(etype, space, pid):
        key = json.dumps({"type": etype, "space": space, "principle": pid}, sort_keys=True)
        return hashlib.md5(key.encode()).hexdigest()

    new_hash = _config_hash(experiment_type, search_space, principle_id)

    for exp in existing_experiments:
        existing_hash = _config_hash(
            exp.get("experiment_type", ""),
            exp.get("search_space", {}),
            exp.get("principle_id"),
        )
        if new_hash == existing_hash:
            return True

    return False


@dataclass
class ExperimentCycle:
    """A batch of experiments evaluated together with shared holdout and FDR."""
    name: str
    holdout_start_date: date
    holdout_end_date: date
    fdr_rate: float = 0.10
    compute_budget_seconds: int = 7200
    max_experiments: int = 20
    status: str = "proposed"
    discovery_snapshot: Optional[Dict[str, Any]] = None
    principles_consulted: Optional[List[str]] = None
    summary: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a JSON-friendly dictionary."""
        return {
            "name": self.name,
            "holdout_start_date": str(self.holdout_start_date),
            "holdout_end_date": str(self.holdout_end_date),
            "fdr_rate": self.fdr_rate,
            "compute_budget_seconds": self.compute_budget_seconds,
            "max_experiments": self.max_experiments,
            "status": self.status,
            "discovery_snapshot": self.discovery_snapshot,
            "principles_consulted": self.principles_consulted,
            "summary": self.summary,
        }


@dataclass
class ExperimentConfig:
    """Base configuration for all experiment types."""
    name: str
    experiment_type: str
    search_space: Dict[str, Any]
    objective_metric: str = "sharpe_ratio"
    objective_direction: str = "maximize"  # or minimize
    max_trials: int = 50

    # Search method: 'grid', 'random', or 'bayesian'
    search_method: str = "grid"

    # Goal (optional) - enables targeted experiments
    goal_target: Optional[float] = None      # e.g., 2.0 for "Sharpe > 2.0"
    goal_type: Optional[str] = None          # 'achieve', 'improve', 'minimize'
    baseline_value: Optional[float] = None   # For 'improve': current value to beat
    early_stop_on_goal: bool = False         # Stop when goal achieved?

    # Backtest settings (for strategy experiments)
    symbols: Optional[List[str]] = None
    exchange: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None

    # Additional config stored as JSON
    extra_config: Dict[str, Any] = field(default_factory=dict)

    # Autonomous experimentation fields
    holdout_config: Optional[Dict[str, Any]] = None   # {holdout_weeks, holdout_start_date, holdout_end_date}
    data_split: Optional[Dict[str, Any]] = None       # {train_start, train_end, validation_start, validation_end}
    principle_id: Optional[str] = None                 # slug referencing YAML principle
    null_hypothesis: Optional[str] = None
    cv_config: Optional[Dict[str, Any]] = None         # {n_splits, embargo_pct, prediction_horizon}
    resource_limits: Optional[Dict[str, Any]] = None   # {max_wall_seconds, max_disk_mb, max_memory_mb}

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a JSON-friendly dictionary."""
        d: Dict[str, Any] = {}
        for f in self.__dataclass_fields__:
            val = getattr(self, f)
            if isinstance(val, dict):
                d[f] = dict(val)  # shallow copy
            elif isinstance(val, list):
                d[f] = list(val)
            else:
                d[f] = val
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ExperimentConfig":
        """Reconstruct an ExperimentConfig from a dictionary."""
        # Only pass keys that are valid dataclass fields
        valid_keys = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in d.items() if k in valid_keys}
        return cls(**filtered)


class ExperimentRunner:
    """
    Executes experiments and tracks results.

    Manages experiment lifecycle:
    - propose(): Create experiment with 'proposed' status
    - approve(): Mark as approved and ready to run
    - reject(): Mark as rejected
    - run(): Execute trials and track results
    - get(): Retrieve experiment details
    - list(): List experiments by status
    """

    def __init__(self, db_url: str):
        self.db_url = db_url

    def _get_conn(self):
        """Get database connection."""
        return psycopg.connect(self.db_url)

    def propose(self, config: ExperimentConfig, proposed_by: str = "ai") -> int:
        """
        Propose a new experiment. Returns experiment_id.
        Status will be 'proposed' until approved.
        """
        with create_span("experiments.core.propose", experiment_name=config.name, experiment_type=config.experiment_type) as span:
            with self._get_conn() as conn:
                with conn.cursor() as cur:
                    # Build config dict for storage
                    full_config = {
                        "symbols": config.symbols,
                        "exchange": config.exchange,
                        "start_date": config.start_date,
                        "end_date": config.end_date,
                        "max_trials": config.max_trials,
                        "search_method": config.search_method,
                        **config.extra_config
                    }

                    cur.execute("""
                        INSERT INTO experiments (
                            name, experiment_type, config, search_space,
                            objective_metric, objective_direction,
                            goal_target, goal_type, baseline_value, early_stop_on_goal,
                            status, proposed_by, total_trials
                        ) VALUES (
                            %s, %s, %s, %s,
                            %s, %s,
                            %s, %s, %s, %s,
                            'proposed', %s, %s
                        )
                        RETURNING id
                    """, (
                        config.name,
                        config.experiment_type,
                        json.dumps(full_config),
                        json.dumps(config.search_space),
                        config.objective_metric,
                        config.objective_direction,
                        config.goal_target,
                        config.goal_type,
                        config.baseline_value,
                        config.early_stop_on_goal,
                        proposed_by,
                        config.max_trials
                    ))

                    experiment_id = cur.fetchone()[0]
                    conn.commit()

            set_attributes(span, experiment_id=experiment_id)
            return experiment_id

    def get(self, experiment_id: int) -> Dict[str, Any]:
        """Get experiment details by ID."""
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT
                        id, name, experiment_type, config, search_space,
                        objective_metric, objective_direction,
                        goal_target, goal_type, baseline_value, early_stop_on_goal,
                        status, priority,
                        parent_experiment_id, depends_on_output,
                        results, artifacts_path, goal_achieved,
                        proposed_by, approved_by,
                        created_at, started_at, completed_at,
                        total_trials, completed_trials, best_score
                    FROM experiments
                    WHERE id = %s
                """, (experiment_id,))

                row = cur.fetchone()
                if row is None:
                    raise ValueError(f"Experiment {experiment_id} not found")

                return {
                    "id": row[0],
                    "name": row[1],
                    "experiment_type": row[2],
                    "config": row[3],
                    "search_space": row[4],
                    "objective_metric": row[5],
                    "objective_direction": row[6],
                    "goal_target": float(row[7]) if row[7] is not None else None,
                    "goal_type": row[8],
                    "baseline_value": float(row[9]) if row[9] is not None else None,
                    "early_stop_on_goal": row[10],
                    "status": row[11],
                    "priority": row[12],
                    "parent_experiment_id": row[13],
                    "depends_on_output": row[14],
                    "results": row[15],
                    "artifacts_path": row[16],
                    "goal_achieved": row[17],
                    "proposed_by": row[18],
                    "approved_by": row[19],
                    "created_at": row[20],
                    "started_at": row[21],
                    "completed_at": row[22],
                    "total_trials": row[23],
                    "completed_trials": row[24],
                    "best_score": float(row[25]) if row[25] is not None else None,
                }

    def approve(self, experiment_id: int, approver: str = "user") -> None:
        """Mark experiment as approved and ready to run."""
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                # Check current status
                cur.execute(
                    "SELECT status FROM experiments WHERE id = %s",
                    (experiment_id,)
                )
                row = cur.fetchone()
                if row is None:
                    raise ValueError(f"Experiment {experiment_id} not found")

                if row[0] != "proposed":
                    raise ValueError(
                        f"Cannot approve experiment with status '{row[0]}'. "
                        "Only 'proposed' experiments can be approved."
                    )

                cur.execute("""
                    UPDATE experiments
                    SET status = 'approved', approved_by = %s
                    WHERE id = %s
                """, (approver, experiment_id))

                conn.commit()

    def reject(self, experiment_id: int, reason: str = None) -> None:
        """Reject a proposed experiment."""
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                # Check current status
                cur.execute(
                    "SELECT status FROM experiments WHERE id = %s",
                    (experiment_id,)
                )
                row = cur.fetchone()
                if row is None:
                    raise ValueError(f"Experiment {experiment_id} not found")

                if row[0] != "proposed":
                    raise ValueError(
                        f"Cannot reject experiment with status '{row[0]}'. "
                        "Only 'proposed' experiments can be rejected."
                    )

                # Store reason in results if provided
                results = {"rejection_reason": reason} if reason else None

                cur.execute("""
                    UPDATE experiments
                    SET status = 'rejected', results = %s
                    WHERE id = %s
                """, (json.dumps(results) if results else None, experiment_id))

                conn.commit()

    def get_pending_approvals(self) -> List[Dict[str, Any]]:
        """Get all experiments awaiting approval."""
        return self.list(status="proposed")

    def list(
        self,
        status: Optional[str] = None,
        experiment_type: Optional[str] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """List experiments with optional filters."""
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                query = """
                    SELECT
                        id, name, experiment_type, status,
                        objective_metric, goal_target, goal_type,
                        proposed_by, approved_by,
                        created_at, completed_at,
                        total_trials, completed_trials, best_score
                    FROM experiments
                    WHERE 1=1
                """
                params = []

                if status:
                    query += " AND status = %s"
                    params.append(status)

                if experiment_type:
                    query += " AND experiment_type = %s"
                    params.append(experiment_type)

                query += " ORDER BY created_at DESC LIMIT %s"
                params.append(limit)

                cur.execute(query, params)

                results = []
                for row in cur.fetchall():
                    results.append({
                        "id": row[0],
                        "name": row[1],
                        "experiment_type": row[2],
                        "status": row[3],
                        "objective_metric": row[4],
                        "goal_target": float(row[5]) if row[5] is not None else None,
                        "goal_type": row[6],
                        "proposed_by": row[7],
                        "approved_by": row[8],
                        "created_at": row[9],
                        "completed_at": row[10],
                        "total_trials": row[11],
                        "completed_trials": row[12],
                        "best_score": float(row[13]) if row[13] is not None else None,
                    })

                return results

    def run(self, experiment_id: int) -> Dict[str, Any]:
        """
        Execute an approved experiment.

        Runs trials using the configured search strategy, stores results,
        and handles goal checking and early stopping.

        Returns:
            Dict with status, completed_trials, best_params, best_score
        """
        from gefion.experiments.search import GridSearch, RandomSearch
        from gefion.experiments.types.strategy_params import StrategyParamExperiment

        # Verify experiment is approved
        experiment = self.get(experiment_id)
        if experiment["status"] != "approved":
            raise ValueError(
                f"Cannot run experiment with status '{experiment['status']}'. "
                "Only 'approved' experiments can be run."
            )

        # Update status to running
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE experiments
                    SET status = 'running', started_at = %s
                    WHERE id = %s
                """, (datetime.now(), experiment_id))
                conn.commit()

        try:
            # Extract experiment configuration
            config = experiment["config"]
            search_space = experiment["search_space"]
            objective_metric = experiment["objective_metric"]
            objective_direction = experiment["objective_direction"]
            goal_type = experiment.get("goal_type")
            goal_target = experiment.get("goal_target")
            baseline_value = experiment.get("baseline_value")
            early_stop = experiment.get("early_stop_on_goal", False)
            max_trials = config.get("max_trials", 50)

            # Create experiment evaluator based on type
            if experiment["experiment_type"] == "strategy_params":
                evaluator = StrategyParamExperiment(
                    strategy_name=config.get("strategy", "momentum"),
                    search_space=search_space,
                    symbols=config.get("symbols", []),
                    start_date=config.get("start_date", "2024-01-01"),
                    end_date=config.get("end_date", "2024-06-01"),
                    objective=objective_metric,
                    db_url=self.db_url,
                )
            else:
                raise ValueError(f"Unknown experiment type: {experiment['experiment_type']}")

            # Create search strategy based on config
            search_method = config.get("search_method", "grid")
            if search_method == "bayesian":
                from gefion.experiments.search import BayesianSearch
                search = BayesianSearch(
                    search_space,
                    direction=objective_direction,
                    max_trials=max_trials,
                )
            elif search_method == "random":
                from gefion.experiments.search import RandomSearch
                search = RandomSearch(search_space, max_trials=max_trials)
            else:  # default to grid
                search = GridSearch(search_space)

            # Track results
            best_score = None
            best_params = None
            completed_trials = 0
            goal_achieved = False

            # Run trials
            trial_number = 0
            while True:
                # Get next params from search strategy
                params = search.suggest()
                if params is None:
                    break  # Search exhausted

                if trial_number >= max_trials:
                    break  # Max trials reached

                trial_number += 1
                trial_start = datetime.now()

                # Evaluate params
                metrics = evaluator.evaluate(params)
                score = metrics.get(objective_metric, 0.0)

                trial_end = datetime.now()
                duration = (trial_end - trial_start).total_seconds()

                # Report to search strategy
                search.report(params, score)

                # Store trial result
                self._store_trial(
                    experiment_id=experiment_id,
                    trial_number=trial_number,
                    params=params,
                    metrics=metrics,
                    score=score,
                    started_at=trial_start,
                    completed_at=trial_end,
                    duration=duration,
                )

                completed_trials += 1

                # Update best score
                if best_score is None:
                    best_score = score
                    best_params = params
                elif objective_direction == "maximize" and score > best_score:
                    best_score = score
                    best_params = params
                elif objective_direction == "minimize" and score < best_score:
                    best_score = score
                    best_params = params

                # Update experiment progress
                self._update_progress(experiment_id, completed_trials, best_score)

                # Check goal achievement
                if goal_type and goal_target is not None:
                    goal_achieved = self._check_goal(
                        score=best_score,
                        goal_type=goal_type,
                        goal_target=goal_target,
                        baseline_value=baseline_value,
                        direction=objective_direction,
                    )

                    if goal_achieved and early_stop:
                        break

            # Mark experiment as completed
            self._complete_experiment(
                experiment_id=experiment_id,
                best_params=best_params,
                best_score=best_score,
                completed_trials=completed_trials,
                goal_achieved=goal_achieved if goal_type else None,
            )

            return {
                "experiment_id": experiment_id,
                "status": "completed",
                "completed_trials": completed_trials,
                "best_params": best_params,
                "best_score": best_score,
                "goal_achieved": goal_achieved if goal_type else None,
            }

        except Exception as e:
            # Mark experiment as failed
            self._fail_experiment(experiment_id, str(e))
            raise

    def _store_trial(
        self,
        experiment_id: int,
        trial_number: int,
        params: Dict[str, Any],
        metrics: Dict[str, Any],
        score: float,
        started_at: datetime,
        completed_at: datetime,
        duration: float,
    ) -> None:
        """Store a trial result in the database."""
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO experiment_trials (
                        experiment_id, trial_number, params, metrics, score,
                        started_at, completed_at, duration_seconds
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    experiment_id,
                    trial_number,
                    json.dumps(params),
                    json.dumps(metrics),
                    score,
                    started_at,
                    completed_at,
                    duration,
                ))
                conn.commit()

    def _update_progress(
        self,
        experiment_id: int,
        completed_trials: int,
        best_score: Optional[float],
    ) -> None:
        """Update experiment progress."""
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE experiments
                    SET completed_trials = %s, best_score = %s
                    WHERE id = %s
                """, (completed_trials, best_score, experiment_id))
                conn.commit()

    def _check_goal(
        self,
        score: float,
        goal_type: str,
        goal_target: float,
        baseline_value: Optional[float],
        direction: str,
    ) -> bool:
        """
        Check if goal is achieved.

        Args:
            score: Current best score
            goal_type: 'achieve', 'improve', or 'minimize'
            goal_target: Target value
            baseline_value: For 'improve', the baseline to beat
            direction: 'maximize' or 'minimize'

        Returns:
            True if goal is achieved
        """
        if goal_type == "achieve":
            if direction == "maximize":
                return score >= goal_target
            else:
                return score <= goal_target

        elif goal_type == "improve":
            if baseline_value is None:
                return False
            # Check if we beat the target (which should be better than baseline)
            if direction == "maximize":
                return score >= goal_target
            else:
                return score <= goal_target

        elif goal_type == "minimize":
            return score <= goal_target

        return False

    def _complete_experiment(
        self,
        experiment_id: int,
        best_params: Optional[Dict[str, Any]],
        best_score: Optional[float],
        completed_trials: int,
        goal_achieved: Optional[bool],
    ) -> None:
        """Mark experiment as completed with results."""
        results = {
            "best_params": best_params,
            "best_score": best_score,
            "completed_trials": completed_trials,
        }

        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE experiments
                    SET status = 'completed',
                        completed_at = %s,
                        results = %s,
                        best_score = %s,
                        completed_trials = %s,
                        goal_achieved = %s
                    WHERE id = %s
                """, (
                    datetime.now(),
                    json.dumps(results),
                    best_score,
                    completed_trials,
                    goal_achieved,
                    experiment_id,
                ))
                conn.commit()

    def _fail_experiment(self, experiment_id: int, error_message: str) -> None:
        """Mark experiment as failed."""
        results = {"error": error_message}

        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE experiments
                    SET status = 'failed',
                        completed_at = %s,
                        results = %s
                    WHERE id = %s
                """, (datetime.now(), json.dumps(results), experiment_id))
                conn.commit()

    def get_results(self, experiment_id: int) -> Dict[str, Any]:
        """Get results for a completed experiment."""
        experiment = self.get(experiment_id)
        return {
            "experiment_id": experiment_id,
            "status": experiment["status"],
            "results": experiment["results"],
            "best_score": experiment["best_score"],
            "completed_trials": experiment["completed_trials"],
            "total_trials": experiment["total_trials"],
            "goal_achieved": experiment["goal_achieved"],
        }

    def chain(
        self,
        parent_id: int,
        child_config: ExperimentConfig,
        depends_on: str
    ) -> int:
        """
        Create a child experiment that depends on parent's output.
        Returns new experiment_id (status='proposed').

        Args:
            parent_id: ID of the parent experiment (must be completed)
            child_config: Configuration for the child experiment
            depends_on: Which parent output to use (best_params, best_score, etc.)

        Returns:
            ID of the new child experiment

        Raises:
            ValueError: If parent doesn't exist or is not completed
        """
        # Verify parent exists and is completed
        parent = self.get(parent_id)
        if parent["status"] != "completed":
            raise ValueError(
                f"Parent experiment {parent_id} is not completed "
                f"(status: {parent['status']}). Cannot chain to incomplete experiment."
            )

        with self._get_conn() as conn:
            with conn.cursor() as cur:
                full_config = {
                    "symbols": child_config.symbols,
                    "exchange": child_config.exchange,
                    "start_date": child_config.start_date,
                    "end_date": child_config.end_date,
                    "max_trials": child_config.max_trials,
                    **child_config.extra_config
                }

                cur.execute("""
                    INSERT INTO experiments (
                        name, experiment_type, config, search_space,
                        objective_metric, objective_direction,
                        goal_target, goal_type, baseline_value, early_stop_on_goal,
                        status, proposed_by, total_trials,
                        parent_experiment_id, depends_on_output
                    ) VALUES (
                        %s, %s, %s, %s,
                        %s, %s,
                        %s, %s, %s, %s,
                        'proposed', 'ai', %s,
                        %s, %s
                    )
                    RETURNING id
                """, (
                    child_config.name,
                    child_config.experiment_type,
                    json.dumps(full_config),
                    json.dumps(child_config.search_space),
                    child_config.objective_metric,
                    child_config.objective_direction,
                    child_config.goal_target,
                    child_config.goal_type,
                    child_config.baseline_value,
                    child_config.early_stop_on_goal,
                    child_config.max_trials,
                    parent_id,
                    depends_on
                ))

                experiment_id = cur.fetchone()[0]
                conn.commit()

        return experiment_id

    def get_parent_results(self, experiment_id: int) -> Optional[Dict[str, Any]]:
        """
        Get results from the parent experiment of a chained experiment.

        Args:
            experiment_id: ID of the child experiment

        Returns:
            Dict with parent's results (best_params, best_score, etc.),
            or None if experiment has no parent.
        """
        experiment = self.get(experiment_id)
        parent_id = experiment.get("parent_experiment_id")

        if parent_id is None:
            return None

        parent = self.get(parent_id)
        results = parent.get("results") or {}

        return {
            "experiment_id": parent_id,
            "name": parent["name"],
            "status": parent["status"],
            "best_params": results.get("best_params"),
            "best_score": parent.get("best_score"),
            "completed_trials": results.get("completed_trials"),
            "depends_on": experiment.get("depends_on_output"),
        }

    def list_children(self, parent_id: int) -> List[Dict[str, Any]]:
        """
        List all child experiments of a parent.

        Args:
            parent_id: ID of the parent experiment

        Returns:
            List of child experiment summaries
        """
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT
                        id, name, experiment_type, status,
                        depends_on_output, created_at, completed_at,
                        best_score
                    FROM experiments
                    WHERE parent_experiment_id = %s
                    ORDER BY created_at DESC
                """, (parent_id,))

                children = []
                for row in cur.fetchall():
                    children.append({
                        "id": row[0],
                        "name": row[1],
                        "experiment_type": row[2],
                        "status": row[3],
                        "depends_on": row[4],
                        "created_at": row[5],
                        "completed_at": row[6],
                        "best_score": float(row[7]) if row[7] is not None else None,
                    })

                return children
