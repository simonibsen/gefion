"""First-class entities for the feature store (spec 007).

The feature registry declares, per feature, which entity table its values
belong to (`entity_table`); identity resolves as the logical key
(entity_table, data_id) instead of a hard-wired FK to stocks. This package is
the cross-cutting layer — none of it is macro-specific:

    registry   — entity-table declaration + validation, safe identifier
                 composition, enumeration of declared tables
    orphans    — per-entity-table integrity scan (consumed by db-health)
    deletion   — registry-driven entity delete (dry-run/confirm)
"""
from gefion.observability import create_span, set_attributes  # noqa: F401
