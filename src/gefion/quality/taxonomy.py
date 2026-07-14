"""Sector/industry taxonomy normalization (issue #86 follow-on).

The 013 sector census exposed taxonomy warts in stocks.sector: AlphaVantage
encodes "unclassifiable" as the literal strings 'None'/'Other' (which the
write path stored verbatim, minting fake sector groups — 94 'NONE' + 41
'OTHER' stocks on prod), and occasionally answers from a different vendor
taxonomy ('FINANCIALS', 'CAPITAL MARKETS') for marginal listings that belong
with 'FINANCIAL SERVICES'.

Normalization is parsing, not rewriting (precedent: _parse_date already
treats 'None' as missing): sentinels become NULL — sector series generation
and sector-scope regime labels already exclude NULL, so unclassifiable
instruments stop polluting groups. Known aliases map to the canonical
sector; everything else is trimmed + uppercased so case variance can never
split a group. Unlike the 008 backfill (findings only, zero stored values
changed), `backfill(apply=True)` here DOES rewrite stocks.sector/industry —
it is a distinct, explicitly-mutating operation, dry-run by default.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from gefion.observability import create_span, set_attributes

# Provider encodings of "unclassified" — never valid group names.
SENTINELS = frozenset({"NONE", "OTHER", "N/A", "-", ""})

# Vendor-taxonomy strays observed in prod (2026-07-13 census), mapped to the
# canonical sector used by the other ~1000 members of the same cohort.
SECTOR_ALIASES: Dict[str, str] = {
    "FINANCIALS": "FINANCIAL SERVICES",
    "CAPITAL MARKETS": "FINANCIAL SERVICES",
}


def _canon(raw: Optional[str]) -> Optional[str]:
    """Trim + uppercase; sentinels (any case) collapse to None."""
    if raw is None:
        return None
    value = str(raw).strip().upper()
    if value in SENTINELS:
        return None
    return value


def normalize_sector(raw: Optional[str]) -> Optional[str]:
    """Canonical sector: sentinel → None, alias → canonical, else TRIM+UPPER."""
    value = _canon(raw)
    if value is None:
        return None
    return SECTOR_ALIASES.get(value, value)


def normalize_industry(raw: Optional[str]) -> Optional[str]:
    """Canonical industry: sentinel → None, else TRIM+UPPER (no alias map —
    the industry vendor split is out of scope until census evidence exists)."""
    return _canon(raw)


def backfill(conn, apply: bool = False) -> Dict[str, Any]:
    """Normalize already-stored stocks.sector/industry values.

    Dry-run by default: reports every (column, from, to) mapping with its
    row count and changes nothing. With apply=True the same mappings are
    written. Idempotent — a second apply finds nothing to change. Originals
    are recoverable from the provider via fundamentals-update.
    """
    with create_span("quality.taxonomy.backfill", apply=apply) as span:
        changes: List[Dict[str, Any]] = []
        rows_changed = 0
        with conn.cursor() as cur:
            for column, normalize in (("sector", normalize_sector),
                                      ("industry", normalize_industry)):
                cur.execute(
                    f"SELECT {column}, count(*) FROM stocks "
                    f"WHERE {column} IS NOT NULL GROUP BY {column}")
                for raw, count in cur.fetchall():
                    target = normalize(raw)
                    if target == raw:
                        continue
                    changes.append({"column": column, "from": raw,
                                    "to": target, "count": count})
                    rows_changed += count
            if apply:
                for c in changes:
                    cur.execute(
                        f"UPDATE stocks SET {c['column']} = %s, "
                        f"updated_at = NOW() WHERE {c['column']} = %s",
                        (c["to"], c["from"]))
        set_attributes(span, rows_changed=rows_changed,
                       mappings=len(changes))
        return {"applied": apply, "rows_changed": rows_changed,
                "changes": changes}
