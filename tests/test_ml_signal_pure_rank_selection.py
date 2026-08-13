"""MLSignalStrategy `selection="pure_rank"` -- rank without a sign floor.

Measured on sloth (2026-08-12): `selection="rank"` (#237) pairs top-K ranking
with a sign floor (long only if q50 > 0, short only if q50 < 0). Arm models
rank well (per-date rank IC positive, both in-sample and out-of-sample), but
the model's LEVEL is biased low (q50 median -0.97%), so the sign floor
converts that level bias into a one-sided book (19-20 shorts vs 5-6 longs).
Both A/B arms lost 100% within 8 weeks, at gross 2.0 and gross 1.0 -- a
one-sided short book in a rising market loses regardless of how well the
names are ordered within each side.

`selection="pure_rank"` trades the ordering and ignores the level: rank the
day's candidates by conviction and long the top K / short the bottom K,
where K = max_positions per side -- NO sign floor. A name is shorted because
it ranks in the bottom K, not because its q50 (or net probability) is
negative. Balanced by construction whenever there are >= 2K candidates.
`return_threshold` (quantile) / `confidence_threshold` (classifier) do not
apply as entry gates in this mode -- they are level filters, and the whole
point is to ignore the level. `downside_limit` keeps applying to longs.

`"absolute"` and `"rank"` are untouched -- this is additive.
"""
from __future__ import annotations

from gefion.strategies.ml_signal import MLSignalStrategy


# ---------------------------------------------------------------------------
# Quantile path
# ---------------------------------------------------------------------------

def _quantile_preds(n_bull, n_bear, bull_q50=None, bear_q50=None):
    """n_bull bullish + n_bear bearish quantile predictions, ranked by strength."""
    preds, prices = {}, {}
    for i in range(n_bull):
        s = f"BULL{i}"
        q50 = bull_q50(i) if bull_q50 else 0.05 + 0.01 * i
        preds[s] = {"q50": q50, "q10": 0.0, "q90": 0.2}
        prices[s] = 100.0
    for i in range(n_bear):
        s = f"BEAR{i}"
        q50 = bear_q50(i) if bear_q50 else -0.05 - 0.01 * i
        preds[s] = {"q50": q50, "q10": -0.2, "q90": 0.0}
        prices[s] = 100.0
    return preds, prices


def test_quantile_pure_rank_direction_top_k_each_side():
    """Planted signal: top-K by q50 go long, bottom-K go short."""
    strat = MLSignalStrategy(mode="long_short", max_positions=3,
                             selection="pure_rank", position_size=0.1)
    preds, prices = _quantile_preds(10, 10)
    signals = strat._generate_quantile_signals_from_predictions(
        preds, {}, prices, 100_000.0)

    buys = [s for s in signals if s["action"] == "buy"]
    shorts = [s for s in signals if s["action"] == "short"]

    assert len(buys) == 3
    assert len(shorts) == 3
    assert {s["symbol"] for s in buys} == {"BULL9", "BULL8", "BULL7"}
    assert {s["symbol"] for s in shorts} == {"BEAR9", "BEAR8", "BEAR7"}


def test_quantile_pure_rank_balances_book_under_negative_bias():
    """The regression pure_rank exists for: a distribution where nearly all
    q50 are negative (the real, measured condition: median -0.97%) must
    still produce a K/K book -- the sign floor in "rank" mode is exactly
    what breaks this, and pure_rank has no sign floor."""
    K = 5
    # 5 weakly-negative names, 15 strongly-negative names. Under "rank" mode
    # every single name fails the long-side sign check (q50 > 0) -- 0 longs.
    preds, prices = _quantile_preds(
        n_bull=5, n_bear=15,
        bull_q50=lambda i: -0.005 + 0.0005 * i,  # -0.005..-0.0028, still < 0
        bear_q50=lambda i: -0.05 - 0.01 * i,     # -0.05..-0.19
    )
    all_preds = dict(preds)
    assert all(p["q50"] < 0 for p in all_preds.values()), "fixture sanity: all negative"

    rank_strat = MLSignalStrategy(mode="long_short", max_positions=K,
                                  selection="rank", position_size=0.1)
    rank_signals = rank_strat._generate_quantile_signals_from_predictions(
        preds, {}, prices, 100_000.0)
    assert len([s for s in rank_signals if s["action"] == "buy"]) == 0, (
        "fixture sanity: rank mode's sign floor starves the long side entirely")

    pure_rank_strat = MLSignalStrategy(mode="long_short", max_positions=K,
                                       selection="pure_rank", position_size=0.1)
    pure_rank_signals = pure_rank_strat._generate_quantile_signals_from_predictions(
        preds, {}, prices, 100_000.0)
    buys = [s for s in pure_rank_signals if s["action"] == "buy"]
    shorts = [s for s in pure_rank_signals if s["action"] == "short"]

    assert len(buys) == K, f"pure_rank must fill the long side to K: {len(buys)}"
    assert len(shorts) == K
    # No sign floor: the "long" names are still negative -- least-bad, not good.
    assert all(preds[s["symbol"]]["q50"] < 0 for s in buys)
    assert {s["symbol"] for s in buys} == {"BULL4", "BULL3", "BULL2", "BULL1", "BULL0"}


def test_quantile_pure_rank_shorts_positive_q50_name_when_ranks_bottom_k():
    """Deliberate behavioral difference from "rank": a name with a positive
    q50 gets shorted if it ranks in the bottom K. Do not "fix" this later --
    it is the point of pure_rank."""
    preds = {
        "A": {"q50": 0.09, "q10": 0.0, "q90": 0.15},
        "B": {"q50": 0.05, "q10": 0.0, "q90": 0.10},
        "C": {"q50": 0.03, "q10": 0.0, "q90": 0.08},
        "D": {"q50": 0.01, "q10": 0.0, "q90": 0.05},
    }
    prices = {s: 100.0 for s in preds}
    strat = MLSignalStrategy(mode="long_short", max_positions=2,
                             selection="pure_rank", position_size=0.1)
    signals = strat._generate_quantile_signals_from_predictions(
        preds, {}, prices, 100_000.0)

    buys = {s["symbol"] for s in signals if s["action"] == "buy"}
    shorts = {s["symbol"] for s in signals if s["action"] == "short"}

    assert buys == {"A", "B"}
    assert shorts == {"C", "D"}
    assert all(preds[s]["q50"] > 0 for s in shorts), (
        "C and D have positive q50 but rank bottom-K -- must still be shorted")


def test_quantile_pure_rank_thin_day_no_crossover():
    """Fewer than 2K candidates: take what exists without crossing over --
    a name must not be both long and short."""
    K = 5
    preds = {}
    for i, q50 in enumerate([0.09, 0.07, 0.05, 0.03, 0.01, -0.02, -0.04]):
        preds[f"S{i}"] = {"q50": q50, "q10": 0.0, "q90": 0.1}
    prices = {s: 100.0 for s in preds}

    strat = MLSignalStrategy(mode="long_short", max_positions=K,
                             selection="pure_rank", position_size=0.1)
    signals = strat._generate_quantile_signals_from_predictions(
        preds, {}, prices, 100_000.0)

    buys = {s["symbol"] for s in signals if s["action"] == "buy"}
    shorts = {s["symbol"] for s in signals if s["action"] == "short"}

    assert buys.isdisjoint(shorts), "a name must not be both long and short"
    assert len(buys) == 5, "top 5 of 7 candidates fill the long side to K"
    assert buys == {"S0", "S1", "S2", "S3", "S4"}
    assert shorts == {"S5", "S6"}, "remaining 2 candidates fill the short side"


def test_quantile_pure_rank_respects_downside_limit_for_longs():
    """downside_limit still applies to longs in pure_rank mode."""
    strat = MLSignalStrategy(mode="long_short", max_positions=5,
                             selection="pure_rank", downside_limit=-0.05,
                             position_size=0.1)
    preds = {
        "GOOD": {"q50": 0.01, "q10": 0.0, "q90": 0.05},
        "BAD_DOWNSIDE": {"q50": 0.02, "q10": -0.30, "q90": 0.10},
    }
    prices = {"GOOD": 100.0, "BAD_DOWNSIDE": 100.0}
    signals = strat._generate_quantile_signals_from_predictions(
        preds, {}, prices, 100_000.0)
    buy_symbols = {s["symbol"] for s in signals if s["action"] == "buy"}
    assert buy_symbols == {"GOOD"}
    # BAD_DOWNSIDE is excluded from longs by downside_limit -- but downside_limit
    # is a long-side filter only, so it remains eligible on the short side.
    short_symbols = {s["symbol"] for s in signals if s["action"] == "short"}
    assert "BAD_DOWNSIDE" in short_symbols


def test_quantile_pure_rank_ignores_return_threshold():
    """return_threshold does not apply in pure_rank mode -- it's a level
    filter and pure_rank trades the ordering, not the level."""
    preds = {
        "A": {"q50": 0.005, "q10": 0.0, "q90": 0.01},
        "B": {"q50": 0.003, "q10": 0.0, "q90": 0.01},
        "C": {"q50": -0.003, "q10": 0.0, "q90": 0.01},
        "D": {"q50": -0.005, "q10": 0.0, "q90": 0.01},
    }
    prices = {s: 100.0 for s in preds}
    # An explicit return_threshold far above any candidate's |q50| would zero
    # out both sides under "absolute"/"rank" -- pure_rank must ignore it.
    strat = MLSignalStrategy(mode="long_short", max_positions=2,
                             selection="pure_rank", return_threshold=0.5,
                             position_size=0.1)
    signals = strat._generate_quantile_signals_from_predictions(
        preds, {}, prices, 100_000.0)
    buys = {s["symbol"] for s in signals if s["action"] == "buy"}
    shorts = {s["symbol"] for s in signals if s["action"] == "short"}
    assert buys == {"A", "B"}
    assert shorts == {"C", "D"}


def test_quantile_pure_rank_default_return_threshold_is_zero():
    strat = MLSignalStrategy(selection="pure_rank")
    assert strat.selection == "pure_rank"
    assert strat.return_threshold == 0.0


def test_absolute_and_rank_unaffected_by_pure_rank_addition():
    """Other modes unchanged: adding pure_rank must not perturb absolute/rank."""
    preds, prices = _quantile_preds(10, 10)

    absolute_strat = MLSignalStrategy(mode="long_short", max_positions=3,
                                      selection="absolute", position_size=0.1)
    rank_strat = MLSignalStrategy(mode="long_short", max_positions=3,
                                  selection="rank", position_size=0.1)

    absolute_signals = absolute_strat._generate_quantile_signals_from_predictions(
        preds, {}, prices, 100_000.0)
    rank_signals = rank_strat._generate_quantile_signals_from_predictions(
        preds, {}, prices, 100_000.0)

    assert {s["symbol"] for s in absolute_signals if s["action"] == "buy"} == \
        {"BULL9", "BULL8", "BULL7"}
    assert {s["symbol"] for s in absolute_signals if s["action"] == "short"} == \
        {"BEAR9", "BEAR8", "BEAR7"}
    assert {s["symbol"] for s in rank_signals if s["action"] == "buy"} == \
        {"BULL9", "BULL8", "BULL7"}
    assert {s["symbol"] for s in rank_signals if s["action"] == "short"} == \
        {"BEAR9", "BEAR8", "BEAR7"}


# ---------------------------------------------------------------------------
# Classifier path
# ---------------------------------------------------------------------------

def _classifier_candidate(up_prob=0.0, down_prob=0.0, predicted_class="neutral"):
    """A classifier prediction row with a fully-specified probability vector,
    so the pure_rank net-probability score (p_up - p_down) is exactly
    controllable in tests."""
    return {
        "predicted_class": predicted_class,
        "p_strong_up": up_prob,
        "p_weak_up": 0.0,
        "p_neutral": max(0.0, 1.0 - up_prob - down_prob),
        "p_weak_down": 0.0,
        "p_strong_down": down_prob,
        "margin": max(up_prob, down_prob),
    }


def test_classifier_pure_rank_direction_top_k_each_side():
    preds, prices = {}, {}
    for i in range(10):
        s = f"BULL{i}"
        preds[s] = _classifier_candidate(up_prob=0.50 + 0.01 * i, predicted_class="strong_up")
        prices[s] = 100.0
    for i in range(10):
        s = f"BEAR{i}"
        preds[s] = _classifier_candidate(down_prob=0.50 + 0.01 * i, predicted_class="strong_down")
        prices[s] = 100.0

    strat = MLSignalStrategy(mode="long_short", max_positions=3,
                             prediction_type="classifier", selection="pure_rank",
                             position_size=0.1)
    signals = strat._generate_classifier_signals_from_predictions(
        preds, {}, prices, 100_000.0)

    buys = {s["symbol"] for s in signals if s["action"] == "buy"}
    shorts = {s["symbol"] for s in signals if s["action"] == "short"}
    assert buys == {"BULL9", "BULL8", "BULL7"}
    assert shorts == {"BEAR9", "BEAR8", "BEAR7"}


def test_classifier_pure_rank_balances_book_under_bias():
    """Analogous to the quantile balance regression: a classifier whose
    bullish candidates are only weakly bullish and bearish candidates are
    strongly bearish must still produce K/K under pure_rank, where "rank"
    mode's class-membership floor still recovers K/K (it has no magnitude
    gate) -- pure_rank must too, since it's a strict relaxation."""
    K = 5
    preds, prices = {}, {}
    for i in range(5):
        s = f"WEAK_UP{i}"
        preds[s] = _classifier_candidate(up_prob=0.02 + 0.01 * i, predicted_class="weak_up")
        prices[s] = 100.0
    for i in range(15):
        s = f"STRONG_DOWN{i}"
        preds[s] = _classifier_candidate(down_prob=0.60 + 0.01 * i, predicted_class="strong_down")
        prices[s] = 100.0

    strat = MLSignalStrategy(mode="long_short", max_positions=K,
                             prediction_type="classifier", selection="pure_rank",
                             position_size=0.1)
    signals = strat._generate_classifier_signals_from_predictions(
        preds, {}, prices, 100_000.0)
    buys = [s for s in signals if s["action"] == "buy"]
    shorts = [s for s in signals if s["action"] == "short"]

    assert len(buys) == K
    assert len(shorts) == K
    assert {s["symbol"] for s in buys} == \
        {"WEAK_UP4", "WEAK_UP3", "WEAK_UP2", "WEAK_UP1", "WEAK_UP0"}


def test_classifier_pure_rank_shorts_bullish_class_when_ranks_bottom_k():
    """Deliberate behavioral difference: a name classified bullish
    (predicted_class in trend_classes) gets shorted if its net probability
    score ranks in the bottom K."""
    preds = {
        "A": _classifier_candidate(up_prob=0.90, predicted_class="strong_up"),
        "B": _classifier_candidate(up_prob=0.50, predicted_class="weak_up"),
        "C": _classifier_candidate(up_prob=0.30, predicted_class="weak_up"),
        "D": _classifier_candidate(up_prob=0.01, predicted_class="weak_up"),
    }
    prices = {s: 100.0 for s in preds}
    strat = MLSignalStrategy(mode="long_short", max_positions=2,
                             prediction_type="classifier", selection="pure_rank",
                             position_size=0.1)
    signals = strat._generate_classifier_signals_from_predictions(
        preds, {}, prices, 100_000.0)

    buys = {s["symbol"] for s in signals if s["action"] == "buy"}
    shorts = {s["symbol"] for s in signals if s["action"] == "short"}

    assert buys == {"A", "B"}
    assert shorts == {"C", "D"}
    assert all(preds[s]["predicted_class"] == "weak_up" for s in shorts), (
        "C and D are classified bullish but rank bottom-K -- must still be shorted")


def test_classifier_pure_rank_thin_day_no_crossover():
    K = 5
    scores = [0.9, 0.7, 0.5, 0.3, 0.1, -0.2, -0.4]
    preds, prices = {}, {}
    for i, score in enumerate(scores):
        symbol = f"S{i}"
        if score >= 0:
            preds[symbol] = _classifier_candidate(up_prob=score, predicted_class="weak_up")
        else:
            preds[symbol] = _classifier_candidate(down_prob=-score, predicted_class="weak_down")
        prices[symbol] = 100.0

    strat = MLSignalStrategy(mode="long_short", max_positions=K,
                             prediction_type="classifier", selection="pure_rank",
                             position_size=0.1)
    signals = strat._generate_classifier_signals_from_predictions(
        preds, {}, prices, 100_000.0)
    buys = {s["symbol"] for s in signals if s["action"] == "buy"}
    shorts = {s["symbol"] for s in signals if s["action"] == "short"}

    assert buys.isdisjoint(shorts)
    assert buys == {"S0", "S1", "S2", "S3", "S4"}
    assert shorts == {"S5", "S6"}


def test_classifier_pure_rank_ignores_confidence_threshold():
    preds = {
        "A": _classifier_candidate(up_prob=0.02, predicted_class="weak_up"),
        "B": _classifier_candidate(up_prob=0.01, predicted_class="weak_up"),
        "C": _classifier_candidate(down_prob=0.01, predicted_class="weak_down"),
        "D": _classifier_candidate(down_prob=0.02, predicted_class="weak_down"),
    }
    prices = {s: 100.0 for s in preds}
    # confidence_threshold near 1.0 would zero out every candidate under
    # "absolute" -- pure_rank must ignore it entirely.
    strat = MLSignalStrategy(mode="long_short", max_positions=2,
                             prediction_type="classifier", selection="pure_rank",
                             confidence_threshold=0.99, position_size=0.1)
    signals = strat._generate_classifier_signals_from_predictions(
        preds, {}, prices, 100_000.0)
    buys = {s["symbol"] for s in signals if s["action"] == "buy"}
    shorts = {s["symbol"] for s in signals if s["action"] == "short"}
    assert buys == {"A", "B"}
    assert shorts == {"C", "D"}


def test_classifier_pure_rank_selection_param_accepted():
    strat = MLSignalStrategy(prediction_type="classifier", selection="pure_rank")
    assert strat.selection == "pure_rank"
