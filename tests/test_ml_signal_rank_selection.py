"""MLSignalStrategy `selection="rank"` (#220).

Diagnosis (confirmed, not re-derived here — see #219): the q50->side
assignment is not inverted. A ~99% short book is a negatively-biased model
(q50 median -0.97%, mean -2.5%) meeting a symmetric *absolute* threshold —
far more names clear a `-0.02` short cutoff than a `+0.02` long cutoff, even
though the per-side cap from #210 already limits each side to
`max_positions`.

`selection="rank"` fixes this: instead of an absolute magnitude cutoff, the
floor is just sign agreement (q50 > 0 for longs, q50 < 0 for shorts), and the
top `max_positions` by conviction on each side are taken. Thin days
under-fill rather than trading against the signal's own sign.

Default stays `selection="absolute"` — today's behavior, byte-identical.
"""
from __future__ import annotations

from gefion.strategies.ml_signal import MLSignalStrategy


# ---------------------------------------------------------------------------
# Quantile path
# ---------------------------------------------------------------------------

def _quantile_preds(n_bull, n_bear, bull_q50=None, bear_q50=None):
    """n_bull bullish + n_bear bearish quantile predictions, ranked by strength.

    bull_q50/bear_q50 are optional callables (index -> q50); default to the
    same strong, well-separated values used in test_ml_signal_max_positions.py.
    """
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


def test_quantile_rank_direction_top_k_each_side():
    """Planted signal: top-K by q50 go long, bottom-K go short."""
    strat = MLSignalStrategy(mode="long_short", max_positions=3,
                             selection="rank", position_size=0.1)
    preds, prices = _quantile_preds(10, 10)
    signals = strat._generate_quantile_signals_from_predictions(
        preds, {}, prices, 100_000.0)

    buys = [s for s in signals if s["action"] == "buy"]
    shorts = [s for s in signals if s["action"] == "short"]

    assert len(buys) == 3
    assert len(shorts) == 3
    assert {s["symbol"] for s in buys} == {"BULL9", "BULL8", "BULL7"}
    assert {s["symbol"] for s in shorts} == {"BEAR9", "BEAR8", "BEAR7"}
    assert all(preds[s["symbol"]]["q50"] > 0 for s in buys)
    assert all(preds[s["symbol"]]["q50"] < 0 for s in shorts)


def test_quantile_rank_balances_book_under_negative_bias():
    """The regression that defines #220: a negatively-biased distribution
    (most names clear the absolute short cutoff, almost none clear the long
    cutoff) must still produce a ~K/K book under rank selection, where
    absolute mode gives a heavily short-skewed book."""
    K = 5
    # 15 strongly bearish names (all clear the -0.02 absolute short cutoff).
    # 5 weakly bullish names: positive, but below the +0.02 absolute cutoff.
    preds, prices = _quantile_preds(
        n_bull=5, n_bear=15,
        bull_q50=lambda i: 0.001 + 0.001 * i,   # 0.001..0.005, all < 0.02
        bear_q50=lambda i: -0.05 - 0.01 * i,    # -0.05..-0.19, all <= -0.02
    )

    absolute_strat = MLSignalStrategy(mode="long_short", max_positions=K,
                                      selection="absolute", position_size=0.1)
    absolute_signals = absolute_strat._generate_quantile_signals_from_predictions(
        preds, {}, prices, 100_000.0)
    absolute_buys = [s for s in absolute_signals if s["action"] == "buy"]
    absolute_shorts = [s for s in absolute_signals if s["action"] == "short"]
    # Demonstrates the bug: absolute threshold starves the long side even
    # though 5 (=K) sign-agreeing candidates exist.
    assert len(absolute_buys) == 0
    assert len(absolute_shorts) == K

    rank_strat = MLSignalStrategy(mode="long_short", max_positions=K,
                                  selection="rank", position_size=0.1)
    rank_signals = rank_strat._generate_quantile_signals_from_predictions(
        preds, {}, prices, 100_000.0)
    rank_buys = [s for s in rank_signals if s["action"] == "buy"]
    rank_shorts = [s for s in rank_signals if s["action"] == "short"]

    assert len(rank_buys) == K, f"rank mode should fill the long side to K: {len(rank_buys)}"
    assert len(rank_shorts) == K
    assert all(preds[s["symbol"]]["q50"] > 0 for s in rank_buys)


def test_quantile_rank_thin_day_underfills_without_backfill():
    """Fewer than K names have a positive q50 -> fewer than K longs open.
    Shorts must not expand to compensate, and no long may violate the sign
    floor. This is the case a naive "always exactly K/K" implementation
    would fail."""
    K = 5
    preds, prices = _quantile_preds(
        n_bull=3, n_bear=15,
        bull_q50=lambda i: 0.001 + 0.001 * i,
        bear_q50=lambda i: -0.05 - 0.01 * i,
    )

    strat = MLSignalStrategy(mode="long_short", max_positions=K,
                             selection="rank", position_size=0.1)
    signals = strat._generate_quantile_signals_from_predictions(
        preds, {}, prices, 100_000.0)
    buys = [s for s in signals if s["action"] == "buy"]
    shorts = [s for s in signals if s["action"] == "short"]

    assert len(buys) == 3, "only 3 names have positive q50 -- must not backfill to 5"
    assert all(preds[s["symbol"]]["q50"] > 0 for s in buys)
    assert len(shorts) == K, "short side is unaffected by the thin long day"


def test_quantile_rank_respects_downside_limit_for_longs():
    """downside_limit still applies to longs in rank mode (#220: 'stays as-is')."""
    strat = MLSignalStrategy(mode="long_short", max_positions=5,
                             selection="rank", downside_limit=-0.05,
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


def test_quantile_rank_return_threshold_is_optional_extra_magnitude_floor():
    """return_threshold defaults to 0 in rank mode (sign alone suffices), but
    if the caller sets it explicitly, it raises the bar above plain sign."""
    preds = {
        "TINY_POS": {"q50": 0.001, "q10": 0.0, "q90": 0.01},
        "BIG_POS": {"q50": 0.05, "q10": 0.0, "q90": 0.10},
    }
    prices = {"TINY_POS": 100.0, "BIG_POS": 100.0}

    # No return_threshold override -> rank-mode default of 0 -> sign alone
    default_strat = MLSignalStrategy(mode="long_only", max_positions=5,
                                     selection="rank", position_size=0.1)
    assert default_strat.return_threshold == 0.0
    signals = default_strat._generate_quantile_signals_from_predictions(
        preds, {}, prices, 100_000.0)
    assert {s["symbol"] for s in signals if s["action"] == "buy"} == {"TINY_POS", "BIG_POS"}

    # Explicit override -> extra magnitude floor on top of the sign check
    strict_strat = MLSignalStrategy(mode="long_only", max_positions=5,
                                    selection="rank", return_threshold=0.02,
                                    position_size=0.1)
    signals = strict_strat._generate_quantile_signals_from_predictions(
        preds, {}, prices, 100_000.0)
    assert {s["symbol"] for s in signals if s["action"] == "buy"} == {"BIG_POS"}


def test_absolute_mode_default_return_threshold_unchanged():
    """Absolute mode's return_threshold default must stay 0.02 -- #220 must not
    silently change existing configs."""
    strat = MLSignalStrategy()
    assert strat.selection == "absolute"
    assert strat.return_threshold == 0.02


def test_absolute_mode_byte_identical_to_pre_220_behavior():
    """Same predictions, same params -- explicit selection='absolute' (or
    omitting it) must reproduce exactly today's #210 per-side-cap behavior."""
    preds, prices = _quantile_preds(10, 10)

    default_strat = MLSignalStrategy(mode="long_short", max_positions=3,
                                     return_threshold=0.02, position_size=0.1)
    explicit_strat = MLSignalStrategy(mode="long_short", max_positions=3,
                                      return_threshold=0.02, position_size=0.1,
                                      selection="absolute")

    default_signals = default_strat._generate_quantile_signals_from_predictions(
        preds, {}, prices, 100_000.0)
    explicit_signals = explicit_strat._generate_quantile_signals_from_predictions(
        preds, {}, prices, 100_000.0)

    assert default_signals == explicit_signals
    buys = [s for s in default_signals if s["action"] == "buy"]
    shorts = [s for s in default_signals if s["action"] == "short"]
    assert {s["symbol"] for s in buys} == {"BULL9", "BULL8", "BULL7"}
    assert {s["symbol"] for s in shorts} == {"BEAR9", "BEAR8", "BEAR7"}


# ---------------------------------------------------------------------------
# Classifier path
# ---------------------------------------------------------------------------

def _classifier_preds(n_up, n_down, up_prob=None, down_prob=None,
                       up_class="strong_up", down_class="strong_down"):
    preds, prices = {}, {}
    for i in range(n_up):
        s = f"UP{i}"
        prob = up_prob(i) if up_prob else 0.60 + 0.01 * i
        preds[s] = {"predicted_class": up_class, f"p_{up_class}": prob,
                    "margin": prob}
        prices[s] = 100.0
    for i in range(n_down):
        s = f"DN{i}"
        prob = down_prob(i) if down_prob else 0.60 + 0.01 * i
        preds[s] = {"predicted_class": down_class, f"p_{down_class}": prob,
                    "margin": prob}
        prices[s] = 100.0
    return preds, prices


def test_classifier_rank_direction_top_k_each_side():
    strat = MLSignalStrategy(mode="long_short", max_positions=2,
                             prediction_type="classifier", selection="rank",
                             trend_classes=["strong_up"],
                             confidence_threshold=0.5, position_size=0.1)
    preds, prices = _classifier_preds(6, 6)
    signals = strat._generate_classifier_signals_from_predictions(
        preds, {}, prices, 100_000.0)
    buys = [s for s in signals if s["action"] == "buy"]
    shorts = [s for s in signals if s["action"] == "short"]

    assert len(buys) == 2
    assert len(shorts) == 2
    assert {s["symbol"] for s in buys} == {"UP5", "UP4"}
    assert {s["symbol"] for s in shorts} == {"DN5", "DN4"}


def test_classifier_rank_balances_book_under_bias():
    """Analogous balance check for the classifier path: even when bearish
    candidates vastly outnumber bullish ones, each side is independently
    capped at K and rank selection doesn't change that -- the classifier's
    class-membership filter is already a sign floor, not an absolute
    magnitude cutoff, so it doesn't exhibit #220's bug. This test locks in
    that both 'absolute' and 'rank' already balance to K/K here."""
    K = 5
    preds, prices = _classifier_preds(n_up=5, n_down=15)

    for selection in ("absolute", "rank"):
        strat = MLSignalStrategy(mode="long_short", max_positions=K,
                                 prediction_type="classifier", selection=selection,
                                 trend_classes=["strong_up"],
                                 confidence_threshold=0.5, position_size=0.1)
        signals = strat._generate_classifier_signals_from_predictions(
            preds, {}, prices, 100_000.0)
        buys = [s for s in signals if s["action"] == "buy"]
        shorts = [s for s in signals if s["action"] == "short"]
        assert len(buys) == K, f"selection={selection}"
        assert len(shorts) == K, f"selection={selection}"


def test_classifier_rank_thin_day_underfills_without_backfill():
    K = 5
    preds, prices = _classifier_preds(n_up=3, n_down=15)

    strat = MLSignalStrategy(mode="long_short", max_positions=K,
                             prediction_type="classifier", selection="rank",
                             trend_classes=["strong_up"],
                             confidence_threshold=0.5, position_size=0.1)
    signals = strat._generate_classifier_signals_from_predictions(
        preds, {}, prices, 100_000.0)
    buys = [s for s in signals if s["action"] == "buy"]
    shorts = [s for s in signals if s["action"] == "short"]

    assert len(buys) == 3, "only 3 names classified bullish -- must not backfill to 5"
    assert all(preds[s["symbol"]]["predicted_class"] == "strong_up" for s in buys)
    assert len(shorts) == K


def test_classifier_selection_param_accepted_and_defaults_to_absolute():
    strat = MLSignalStrategy(prediction_type="classifier")
    assert strat.selection == "absolute"
