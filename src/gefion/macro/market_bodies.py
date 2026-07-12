"""Seed bodies for market-level functions (spec 011).

These are SEEDS, not the source of truth: `macro derive` plants them
create-if-absent; after that the DATABASE copy is what executes, and
operator edits persist across deploys. `macro derive --reseed <name>` is
the only (explicit, loud) path that overwrites a DB body from here.

Contract: specs/011-market-dispatcher/contracts/function-contract.md —
`compute(rows)` gets ONE date's cross-section, returns float or None.
"""

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
