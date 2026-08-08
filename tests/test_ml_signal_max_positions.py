"""MLSignalStrategy must enforce max_positions PER SIDE in long_short (#210).

Before this fix the long side was sliced to available_slots but the short side
shorted *every* bearish name (no sort, no cap) — the ~2000-short book that
degenerated the NYSE A/B. And the long budget subtracted *all* positions
(`len(positions)`), so a big short book also squeezed longs to ~10. The cap is
now applied independently to each side, ranked by conviction.

This is the mechanical half of #210. The long/short *assignment* question
(is the q50 threshold inverted?) is separate — it needs a planted-signal
directional test and stays supervised.
"""
from __future__ import annotations

from gefion.strategies.ml_signal import MLSignalStrategy


def _quantile_preds(n_bull, n_bear):
    """n_bull bullish + n_bear bearish quantile predictions, ranked by strength."""
    preds, prices = {}, {}
    for i in range(n_bull):
        s = f"BULL{i}"
        preds[s] = {"q50": 0.05 + 0.01 * i, "q10": 0.0, "q90": 0.2}
        prices[s] = 100.0
    for i in range(n_bear):
        s = f"BEAR{i}"
        preds[s] = {"q50": -0.05 - 0.01 * i, "q10": -0.2, "q90": 0.0}
        prices[s] = 100.0
    return preds, prices


def test_quantile_long_short_caps_each_side_at_max_positions():
    strat = MLSignalStrategy(mode="long_short", max_positions=3,
                             return_threshold=0.02, position_size=0.1)
    preds, prices = _quantile_preds(10, 10)
    signals = strat._generate_quantile_signals_from_predictions(
        preds, {}, prices, 100_000.0)

    buys = [s for s in signals if s["action"] == "buy"]
    shorts = [s for s in signals if s["action"] == "short"]

    assert len(buys) == 3, f"long side over max_positions: {len(buys)}"
    assert len(shorts) == 3, f"short side over max_positions: {len(shorts)}"

    # And it keeps the highest-conviction names on each side (ranked, not arbitrary).
    assert {s["symbol"] for s in buys} == {"BULL9", "BULL8", "BULL7"}
    assert {s["symbol"] for s in shorts} == {"BEAR9", "BEAR8", "BEAR7"}


def test_short_cap_accounts_for_existing_shorts_per_side():
    """Existing shorts consume the short budget; longs are unaffected by them."""
    strat = MLSignalStrategy(mode="long_short", max_positions=3,
                             return_threshold=0.02, position_size=0.1)
    preds, prices = _quantile_preds(10, 10)
    positions = {"HELD_A": -50, "HELD_B": -50}  # 2 existing shorts (not in preds)

    signals = strat._generate_quantile_signals_from_predictions(
        preds, positions, prices, 100_000.0)
    shorts = [s for s in signals if s["action"] == "short"]
    buys = [s for s in signals if s["action"] == "buy"]

    assert len(shorts) == 1, "3 budget - 2 existing shorts => 1 new short"
    assert len(buys) == 3, "existing shorts must not steal long slots"


def test_classifier_long_short_caps_each_side_at_max_positions():
    strat = MLSignalStrategy(mode="long_short", max_positions=2,
                             prediction_type="classifier",
                             trend_classes=["strong_up"],
                             confidence_threshold=0.5, position_size=0.1)
    preds, prices = {}, {}
    for i in range(6):
        s = f"UP{i}"
        preds[s] = {"predicted_class": "strong_up",
                    "p_strong_up": 0.60 + 0.01 * i, "margin": 0.1 + 0.01 * i}
        prices[s] = 100.0
    for i in range(6):
        s = f"DN{i}"
        preds[s] = {"predicted_class": "strong_down",
                    "p_strong_down": 0.60 + 0.01 * i, "margin": 0.1}
        prices[s] = 100.0

    signals = strat._generate_classifier_signals_from_predictions(
        preds, {}, prices, 100_000.0)
    buys = [s for s in signals if s["action"] == "buy"]
    shorts = [s for s in signals if s["action"] == "short"]

    assert len(buys) == 2, f"long side over max_positions: {len(buys)}"
    assert len(shorts) == 2, f"short side over max_positions: {len(shorts)}"


def test_long_only_never_shorts_and_respects_cap():
    strat = MLSignalStrategy(mode="long_only", max_positions=3,
                             return_threshold=0.02, position_size=0.1)
    preds, prices = _quantile_preds(10, 10)
    signals = strat._generate_quantile_signals_from_predictions(
        preds, {}, prices, 100_000.0)
    assert not any(s["action"] == "short" for s in signals)
    assert len([s for s in signals if s["action"] == "buy"]) == 3
