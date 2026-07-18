"""Seed bodies for market-level functions (spec 011).

These are SEEDS, not the source of truth: `macro derive` plants them
create-if-absent; after that the DATABASE copy is what executes, and
operator edits persist across deploys. `macro derive --reseed <name>` is
the only (explicit, loud) path that overwrites a DB body from here.

Contract: specs/011-market-dispatcher/contracts/function-contract.md —
`compute(rows)` gets ONE date's cross-section, returns float or None.
"""

def sector_slug(sector: str) -> str:
    """Deterministic slug: lowercase, every non-alphanumeric run -> single
    underscore, trimmed. Collisions between DISTINCT sectors are refused
    loudly at seeding, never merged."""
    import re
    return re.sub(r"[^a-z0-9]+", "_", sector.strip().lower()).strip("_")


def sector_signal_bodies(sector: str, min_members: int = 30) -> dict:
    """Generated market bodies for ONE sector (spec 013): relative strength
    (median member ret_20 minus median all-rows ret_20) and internal breadth
    (% of members above their own 200-day SMA). MIN_MEMBERS is written into
    the body — the declared, operator-editable floor; a thinner (sector,
    date) returns None (gap, never a value). NULL-sector rows are excluded
    from membership but kept in the all-rows market baseline."""
    slug = sector_slug(sector)
    rs_body = (
        'def compute(rows):\n'
        f'    """Median member ret_20 minus median ALL-rows ret_20 ({sector})."""\n'
        f'    MIN_MEMBERS = {min_members}\n'
        '    def median(xs):\n'
        '        xs = sorted(xs)\n'
        '        n = len(xs)\n'
        '        mid = n // 2\n'
        '        return xs[mid] if n % 2 else (xs[mid - 1] + xs[mid]) / 2.0\n'
        '    market = [r["ret_20"] for r in rows if r.get("ret_20") is not None]\n'
        '    members = [r["ret_20"] for r in rows\n'
        f'               if r.get("sector") == {sector!r}\n'
        '               and r.get("ret_20") is not None]\n'
        '    if len(members) < MIN_MEMBERS or not market:\n'
        '        return None\n'
        '    return median(members) - median(market)\n'
    )
    breadth_body = (
        'def compute(rows):\n'
        f'    """% of {sector} members above their own 200-day SMA."""\n'
        f'    MIN_MEMBERS = {min_members}\n'
        '    members = [r for r in rows\n'
        f'               if r.get("sector") == {sector!r}\n'
        '               and r.get("indicator_sma_200") is not None\n'
        '               and r["indicator_sma_200"] > 0]\n'
        '    if len(members) < MIN_MEMBERS:\n'
        '        return None\n'
        '    hits = sum(1 for r in members if r["close"] > r["indicator_sma_200"])\n'
        '    return 100.0 * hits / len(members)\n'
    )
    return {
        f"sector_rs_{slug}": {
            "description": (f"Sector relative strength: median 20-day return "
                            f"of {sector} members minus market median"),
            "inputs": {"features": ["ret_20"]},
            "body": rs_body,
        },
        f"sector_breadth_{slug}": {
            "description": (f"Sector breadth: % of {sector} members closing "
                            f"above their own 200-day SMA"),
            "inputs": {"features": ["indicator_sma_200"]},
            "body": breadth_body,
        },
    }


def model_signal_bodies(model_name: str, model_version: str,
                        horizon: int, cutoff: str) -> dict:
    """Generated market bodies for one vintage model's prediction signals
    (spec 012). Unlike SEED_BODIES these embed the model identity — the
    per-stock feature names they read carry name+version, so a second
    vintage can never silently feed the same series. Seeded create-if-absent
    by `ml materialize-signals`; the DB copy wins afterwards (011 rule)."""
    q10 = f"pred_q10_h{horizon}__{model_name}_{model_version}"
    q50 = f"pred_q50_h{horizon}__{model_name}_{model_version}"
    q90 = f"pred_q90_h{horizon}__{model_name}_{model_version}"
    provenance = (f"model={model_name}:{model_version}, "
                  f"training_cutoff={cutoff}, horizon={horizon}d")
    # median inlined: the sandbox exposes compute() alone, not sibling helpers
    median = '''    vals.sort()
    n = len(vals)
    mid = n // 2
    return vals[mid] if n % 2 else (vals[mid - 1] + vals[mid]) / 2.0
'''
    return {
        "model_outlook_q50": {
            "description": (f"Model outlook: cross-sectional median q50 "
                            f"prediction ({provenance})"),
            "inputs": {"features": [q50]},
            "body": f'''def compute(rows):
    """Median of the model's q50 forecasts across the cross-section."""
    vals = [r["{q50}"] for r in rows if r.get("{q50}") is not None]
    if not vals:
        return None
{median}''',
        },
        "model_confidence_width": {
            "description": (f"Model confidence width: cross-sectional median "
                            f"of q90-q10 spread ({provenance})"),
            "inputs": {"features": [q10, q90]},
            "body": f'''def compute(rows):
    """Median per-stock q90-q10 spread: how unsure the model is today."""
    vals = [r["{q90}"] - r["{q10}"] for r in rows
            if r.get("{q90}") is not None and r.get("{q10}") is not None]
    if not vals:
        return None
{median}''',
        },
    }


SEED_BODIES = {
    "breadth_sma200": {
        "description": ("Breadth: % of Stock universe closing above its own "
                        "200-day SMA (participation)"),
        "inputs": {"features": ["indicator_sma_200"]},
        "body": '''def compute(rows):
    """% of stocks above their own 200-day SMA (participation breadth)."""
    eligible = [r for r in rows
                if r.get("indicator_sma_200") is not None
                and r["indicator_sma_200"] > 0]
    if not eligible:
        return None
    hits = sum(1 for r in eligible if r["close"] > r["indicator_sma_200"])
    return 100.0 * hits / len(eligible)
''',
    },
    "dispersion_20": {
        "description": ("Dispersion: cross-sectional std of 20-day returns "
                        "(when stocks move together, selection can't matter)"),
        "inputs": {"features": ["ret_20"]},
        "body": '''def compute(rows):
    """Population std of the cross-section's 20-day returns."""
    rets = [r["ret_20"] for r in rows if r.get("ret_20") is not None]
    if not rets:
        return None
    n = len(rets)
    mean = sum(rets) / n
    return (sum((x - mean) ** 2 for x in rets) / n) ** 0.5
''',
    },
}


# --- generation templates for candidate market bodies (spec 014) -------------------
#
# Deterministic fallbacks for machine-proposed candidates: keyword-matched on
# the principle id (mirrors the per-stock template path). These land in the
# CANDIDATE ledger, never directly in feature_functions — a human owns the
# gate. Contract: compute(rows) over one date's cross-section, float | None.

MARKET_CANDIDATE_TEMPLATES = {
    "advance_share": (
        'def compute(rows):\n'
        '    """% of stocks whose close is above their low of the day\n'
        '    midpoint — a participation reading."""\n'
        '    scored = [r for r in rows\n'
        '              if r.get("high") is not None and r.get("low") is not None\n'
        '              and r["high"] > r["low"]]\n'
        '    if len(scored) < 30:\n'
        '        return None\n'
        '    up = sum(1 for r in scored\n'
        '             if r["close"] > (r["high"] + r["low"]) / 2.0)\n'
        '    return 100.0 * up / len(scored)\n'
    ),
    "volume_concentration": (
        'def compute(rows):\n'
        '    """Share of total volume carried by the top decile of stocks\n'
        '    by volume — crowding/concentration reading."""\n'
        '    vols = sorted((r["volume"] for r in rows\n'
        '                   if r.get("volume")), reverse=True)\n'
        '    if len(vols) < 50:\n'
        '        return None\n'
        '    top = vols[:max(1, len(vols) // 10)]\n'
        '    total = float(sum(vols))\n'
        '    return 100.0 * sum(top) / total if total > 0 else None\n'
    ),
    "range_dispersion": (
        'def compute(rows):\n'
        '    """Cross-sectional median of (high-low)/close — an intraday\n'
        '    range dispersion reading."""\n'
        '    spans = sorted((r["high"] - r["low"]) / r["close"] for r in rows\n'
        '                   if r.get("high") is not None\n'
        '                   and r.get("low") is not None and r["close"] > 0)\n'
        '    if len(spans) < 30:\n'
        '        return None\n'
        '    mid = len(spans) // 2\n'
        '    return (spans[mid] if len(spans) % 2\n'
        '            else (spans[mid - 1] + spans[mid]) / 2.0)\n'
    ),
}


def market_template_for(principle_id: str):
    """Keyword-match a principle to a candidate market template, or None —
    the honest no-match answer (no forced default: an unmatched principle
    proposes nothing rather than something irrelevant)."""
    pid = principle_id.lower()
    if any(k in pid for k in ("breadth", "participation", "advance")):
        return MARKET_CANDIDATE_TEMPLATES["advance_share"]
    if any(k in pid for k in ("concentration", "crowding", "volume")):
        return MARKET_CANDIDATE_TEMPLATES["volume_concentration"]
    if any(k in pid for k in ("dispersion", "range", "volatility")):
        return MARKET_CANDIDATE_TEMPLATES["range_dispersion"]
    return None


def composite_template_for(principle_id: str, series: list) -> str:
    """Deterministic composite candidate over the DECLARED series: an
    equal-weight mean of the named inputs. Deliberately simple scaffolding —
    the reviewer judges whether the combination deserves better, and the
    Claude path writes richer bodies when available."""
    keys = ", ".join(f"row[{s!r}]" for s in series)
    return (
        'def compute(row):\n'
        f'    """Equal-weight composite over {", ".join(series)}\n'
        f'    (generated from principle: {principle_id})."""\n'
        f'    values = [{keys}]\n'
        '    return sum(values) / len(values)\n'
    )
