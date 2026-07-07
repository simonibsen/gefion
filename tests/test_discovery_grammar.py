"""Grammar tests for agentic regime discovery (006, T005).

TDD: written FIRST. The bounded compositional grammar is where the FDR
denominator comes from: enumeration must be deterministic and EXACT, hashing
canonical (AND(a,b) == AND(b,a)), and the depth cap a hard refusal — an
unbounded or miscounted search space can't be corrected for.
"""
import pytest

from gefion.regimes.discovery import grammar


TERCILE_VOL = {"feature": "realized_vol_20", "form": "tercile"}
TERCILE_ADX = {"feature": "indicator_adx_14", "form": "tercile"}
RSI_HIGH = {"feature": "indicator_rsi_14", "cmp": ">", "value": 70}
RSI_LOW = {"feature": "indicator_rsi_14", "cmp": "<", "value": 30}
VOL_HIGH = {"feature": "realized_vol_20", "cmp": ">", "value": 0.02}

LIBRARY = [TERCILE_VOL, TERCILE_ADX, RSI_HIGH, RSI_LOW, VOL_HIGH]


# --- atom-library validation -----------------------------------------------

def test_validate_atoms_accepts_both_forms():
    atoms = grammar.validate_atoms(LIBRARY)
    assert len(atoms) == 5


def test_validate_atoms_rejects_empty_library():
    with pytest.raises(grammar.GrammarError):
        grammar.validate_atoms([])


def test_validate_atoms_rejects_unknown_shape():
    with pytest.raises(grammar.GrammarError):
        grammar.validate_atoms([{"feature": "x"}])  # neither form nor cmp
    with pytest.raises(grammar.GrammarError):
        grammar.validate_atoms([{"feature": "x", "form": "quartile"}])
    with pytest.raises(grammar.GrammarError):
        grammar.validate_atoms([{"feature": "x", "cmp": "~", "value": 1}])
    with pytest.raises(grammar.GrammarError):
        grammar.validate_atoms([{"cmp": ">", "value": 1}])  # missing feature


def test_validate_atoms_rejects_duplicates():
    with pytest.raises(grammar.GrammarError):
        grammar.validate_atoms([RSI_HIGH, dict(RSI_HIGH)])


# --- deterministic, exact enumeration --------------------------------------

def test_enumeration_deterministic():
    a = grammar.enumerate_candidates(LIBRARY, depth=2)
    b = grammar.enumerate_candidates(LIBRARY, depth=2)
    assert [c.expression for c in a] == [c.expression for c in b]
    # order must not depend on input atom order
    c = grammar.enumerate_candidates(list(reversed(LIBRARY)), depth=2)
    assert [x.expression for x in a] == [x.expression for x in c]


def test_enumeration_depth1_is_one_candidate_per_atom():
    cands = grammar.enumerate_candidates(LIBRARY, depth=1)
    assert len(cands) == len(LIBRARY)
    assert all(c.depth == 1 for c in cands)


def test_enumeration_exact_count_at_depth2():
    """Family denominator input: M singles + AND/OR pairs of BOOLEAN atoms.

    Tercile atoms are 3-bucket (not boolean) so they cannot compose; the
    3 boolean atoms give C(3,2)=3 pairs x {AND, OR} = 6 composites.
    """
    cands = grammar.enumerate_candidates(LIBRARY, depth=2)
    m, m_bool = 5, 3
    expected = m + 2 * (m_bool * (m_bool - 1) // 2)
    assert len(cands) == expected == grammar.search_space_size(LIBRARY, depth=2)


def test_search_space_size_matches_enumeration_depth1():
    assert grammar.search_space_size(LIBRARY, depth=1) == len(
        grammar.enumerate_candidates(LIBRARY, depth=1))


def test_candidates_carry_valid_005_expressions():
    """Every candidate must be an ordinary 005 RegimeExpression."""
    from gefion.regimes.definitions import validate_expression
    for cand in grammar.enumerate_candidates(LIBRARY, depth=2):
        validate_expression(cand.expression)
        assert cand.bucketing["labels"]
        assert cand.atom_features  # for entanglement/availability checks


# --- canonical hashing and dedup -------------------------------------------

def test_hash_is_canonical_across_child_order():
    ast_ab = {"op": "AND", "children": [grammar.atom_leaf(RSI_HIGH), grammar.atom_leaf(VOL_HIGH)]}
    ast_ba = {"op": "AND", "children": [grammar.atom_leaf(VOL_HIGH), grammar.atom_leaf(RSI_HIGH)]}
    assert grammar.candidate_hash(ast_ab) == grammar.candidate_hash(ast_ba)


def test_hash_distinguishes_expressions():
    h1 = grammar.candidate_hash(grammar.atom_leaf(RSI_HIGH))
    h2 = grammar.candidate_hash(grammar.atom_leaf(RSI_LOW))
    ast_and = {"op": "AND", "children": [grammar.atom_leaf(RSI_HIGH), grammar.atom_leaf(VOL_HIGH)]}
    ast_or = {"op": "OR", "children": [grammar.atom_leaf(RSI_HIGH), grammar.atom_leaf(VOL_HIGH)]}
    assert len({h1, h2, grammar.candidate_hash(ast_and), grammar.candidate_hash(ast_or)}) == 4


def test_enumeration_has_no_duplicate_hashes():
    cands = grammar.enumerate_candidates(LIBRARY, depth=2)
    hashes = [grammar.candidate_hash(c.expression) for c in cands]
    assert len(hashes) == len(set(hashes))


# --- depth cap: hard refusal ------------------------------------------------

def test_depth_beyond_hard_cap_refused():
    """K > HARD_DEPTH_CAP must refuse, not silently truncate: raising the cap
    is gated on the FR-108 bootstrap fast-follow."""
    with pytest.raises(grammar.GrammarError):
        grammar.enumerate_candidates(LIBRARY, depth=grammar.HARD_DEPTH_CAP + 1)


def test_depth_must_be_positive():
    with pytest.raises(grammar.GrammarError):
        grammar.enumerate_candidates(LIBRARY, depth=0)


# --- principle seeding (T031, US3) -------------------------------------------

PRINCIPLES = [
    {"id": "hurst-exponent-regime",
     "data_requirements": ["ohlcv.close", "features.rsi", "features.macd"]},
    {"id": "low-volatility-anomaly",
     "data_requirements": ["close", "features.volatility_realized"]},
]

AVAILABLE = ["indicator_rsi_14", "realized_vol_20", "volume_zscore"]


def test_seed_atoms_from_principles_matches_available_features():
    atoms = grammar.seed_atoms_from_principles(PRINCIPLES, AVAILABLE)
    by_feature = {a["feature"]: a for a in atoms}
    # features.rsi -> indicator_rsi_14; features.volatility_realized -> realized_vol_20
    assert "indicator_rsi_14" in by_feature
    assert "realized_vol_20" in by_feature
    # non-feature requirements (ohlcv.close) and unmatched refs produce nothing
    assert "volume_zscore" not in by_feature
    # every seeded atom carries provenance to its principle
    assert by_feature["indicator_rsi_14"]["provenance"]["principle_id"] == \
        "hurst-exponent-regime"
    assert by_feature["realized_vol_20"]["provenance"]["principle_id"] == \
        "low-volatility-anomaly"


def test_seeded_atoms_are_valid_and_deterministic():
    atoms = grammar.seed_atoms_from_principles(PRINCIPLES, AVAILABLE)
    assert grammar.validate_atoms(atoms) == grammar.validate_atoms(
        grammar.seed_atoms_from_principles(PRINCIPLES, AVAILABLE))
    assert atoms  # bounded but non-empty on matching inputs


def test_candidates_carry_principle_provenance():
    atoms = grammar.seed_atoms_from_principles(PRINCIPLES, AVAILABLE)
    cands = grammar.enumerate_candidates(atoms, depth=1)
    assert all(c.principles for c in cands)
    rsi = next(c for c in cands if c.atom_features == ("indicator_rsi_14",))
    assert rsi.principles == ("hurst-exponent-regime",)
