"""Data quality — provider-garbage detection & quarantine (spec 008).

Two populations, two treatments: *degenerate but real* values (a shell
company's ROE of −615% from near-zero revenue is internally consistent) stay
usable and unflagged; *provider trash* (definitionally impossible or
self-contradictory values — Beta −503,341.44) is stored verbatim but convicted
and treated as missing by research consumers unless explicitly opted in.

Only the two high-confidence tiers can convict: definitional bounds and
cross-field contradiction against trusted stored data. Temporal spikes and
cross-sectional outliers corroborate or mark *suspect* — never trash on their
own, because outlierness cannot distinguish trash from distress.

Modules:
    catalog  — load data-quality/catalog.yaml (bounds, derivations, universe)
    rules    — tier evaluators (pure functions)
    findings — the data_quality_findings audit ledger (idempotent upserts)
    validate — bounded batch pass riding the covered write paths
    backfill — on-demand validation of already-stored history
    universe — quality research universes (test tickers, asset_type/exchange)
"""
from gefion.observability import create_span, set_attributes  # noqa: F401
