"""Tests for holdout window management module.

TDD: These tests are written FIRST, before implementation.
"""
import datetime
import pytest

from gefion.experiments.holdout import HoldoutManager


class TestHoldoutDefaults:
    """Tests for default holdout configuration."""

    def test_default_holdout_weeks_is_six(self):
        """HoldoutManager defaults to 6-week holdout window."""
        max_date = datetime.date(2026, 3, 29)
        mgr = HoldoutManager(max_date=max_date)
        assert mgr.holdout_weeks == 6

    def test_default_holdout_start_date(self):
        """Holdout start date is max_date minus holdout_weeks."""
        max_date = datetime.date(2026, 3, 29)
        mgr = HoldoutManager(max_date=max_date)
        expected_start = max_date - datetime.timedelta(weeks=6)
        assert mgr.holdout_start_date == expected_start

    def test_holdout_end_date_is_max_date(self):
        """Holdout end date equals the max date in the data."""
        max_date = datetime.date(2026, 3, 29)
        mgr = HoldoutManager(max_date=max_date)
        assert mgr.holdout_end_date == max_date


class TestHoldoutCustomWeeks:
    """Tests for custom holdout_weeks parameter."""

    def test_custom_holdout_weeks_4(self):
        """Custom 4-week holdout computes correct start date."""
        max_date = datetime.date(2026, 3, 29)
        mgr = HoldoutManager(max_date=max_date, holdout_weeks=4)
        assert mgr.holdout_weeks == 4
        expected_start = max_date - datetime.timedelta(weeks=4)
        assert mgr.holdout_start_date == expected_start

    def test_custom_holdout_weeks_12(self):
        """Custom 12-week holdout computes correct start date."""
        max_date = datetime.date(2026, 6, 1)
        mgr = HoldoutManager(max_date=max_date, holdout_weeks=12)
        expected_start = max_date - datetime.timedelta(weeks=12)
        assert mgr.holdout_start_date == expected_start
        assert mgr.holdout_end_date == max_date


class TestMaxTrainingDate:
    """Tests for get_max_training_date method."""

    def test_max_training_date_is_one_day_before_holdout_start(self):
        """Training data ends the day before the holdout window begins."""
        max_date = datetime.date(2026, 3, 29)
        mgr = HoldoutManager(max_date=max_date, holdout_weeks=6)
        expected_training_end = mgr.holdout_start_date - datetime.timedelta(days=1)
        assert mgr.get_max_training_date() == expected_training_end

    def test_max_training_date_with_custom_weeks(self):
        """Training date adjusts correctly for custom holdout window."""
        max_date = datetime.date(2026, 6, 15)
        mgr = HoldoutManager(max_date=max_date, holdout_weeks=8)
        holdout_start = max_date - datetime.timedelta(weeks=8)
        expected = holdout_start - datetime.timedelta(days=1)
        assert mgr.get_max_training_date() == expected


class TestSerialization:
    """Tests for to_dict / from_dict round-trip."""

    def test_to_dict_contains_required_keys(self):
        """Serialized dict has holdout_weeks, holdout_start_date, holdout_end_date."""
        max_date = datetime.date(2026, 3, 29)
        mgr = HoldoutManager(max_date=max_date, holdout_weeks=6)
        d = mgr.to_dict()
        assert "holdout_weeks" in d
        assert "holdout_start_date" in d
        assert "holdout_end_date" in d

    def test_to_dict_values_are_serializable(self):
        """Dict values are JSON-friendly (strings for dates, int for weeks)."""
        max_date = datetime.date(2026, 3, 29)
        mgr = HoldoutManager(max_date=max_date)
        d = mgr.to_dict()
        assert isinstance(d["holdout_weeks"], int)
        assert isinstance(d["holdout_start_date"], str)
        assert isinstance(d["holdout_end_date"], str)

    def test_from_dict_round_trip(self):
        """from_dict(to_dict()) produces an equivalent HoldoutManager."""
        max_date = datetime.date(2026, 3, 29)
        original = HoldoutManager(max_date=max_date, holdout_weeks=6)
        restored = HoldoutManager.from_dict(original.to_dict())
        assert restored.holdout_weeks == original.holdout_weeks
        assert restored.holdout_start_date == original.holdout_start_date
        assert restored.holdout_end_date == original.holdout_end_date
        assert restored.get_max_training_date() == original.get_max_training_date()

    def test_from_dict_with_custom_weeks(self):
        """Round-trip preserves custom holdout_weeks."""
        max_date = datetime.date(2026, 5, 10)
        original = HoldoutManager(max_date=max_date, holdout_weeks=10)
        restored = HoldoutManager.from_dict(original.to_dict())
        assert restored.holdout_weeks == 10
        assert restored.holdout_start_date == original.holdout_start_date


class TestValidation:
    """Tests for input validation."""

    def test_holdout_weeks_zero_raises(self):
        """holdout_weeks=0 raises ValueError."""
        with pytest.raises(ValueError, match="holdout_weeks"):
            HoldoutManager(max_date=datetime.date(2026, 3, 29), holdout_weeks=0)

    def test_holdout_weeks_negative_raises(self):
        """Negative holdout_weeks raises ValueError."""
        with pytest.raises(ValueError, match="holdout_weeks"):
            HoldoutManager(max_date=datetime.date(2026, 3, 29), holdout_weeks=-3)

    def test_insufficient_data_raises(self):
        """Raises ValueError when data span is less than 2 weeks."""
        max_date = datetime.date(2026, 3, 29)
        min_date = datetime.date(2026, 3, 20)  # only 9 days of data
        with pytest.raises(ValueError, match="2 weeks"):
            HoldoutManager(
                max_date=max_date,
                min_date=min_date,
                holdout_weeks=6,
            )

    def test_sufficient_data_does_not_raise(self):
        """No error when data span exceeds minimum 2 weeks."""
        max_date = datetime.date(2026, 3, 29)
        min_date = datetime.date(2026, 1, 1)  # ~3 months of data
        mgr = HoldoutManager(max_date=max_date, min_date=min_date, holdout_weeks=6)
        assert mgr.holdout_weeks == 6


class TestRollForward:
    """Tests that holdout window uses the most recent data."""

    def test_roll_forward_updates_dates(self):
        """Rolling forward to a new max_date recomputes the holdout window."""
        old_max = datetime.date(2026, 3, 1)
        mgr = HoldoutManager(max_date=old_max, holdout_weeks=6)
        old_start = mgr.holdout_start_date

        new_max = datetime.date(2026, 3, 29)
        mgr_new = HoldoutManager(max_date=new_max, holdout_weeks=6)

        assert mgr_new.holdout_end_date == new_max
        assert mgr_new.holdout_start_date > old_start
        expected_start = new_max - datetime.timedelta(weeks=6)
        assert mgr_new.holdout_start_date == expected_start

    def test_roll_forward_shifts_training_cutoff(self):
        """New data shifts the max training date forward."""
        old_max = datetime.date(2026, 2, 15)
        new_max = datetime.date(2026, 3, 29)

        mgr_old = HoldoutManager(max_date=old_max, holdout_weeks=6)
        mgr_new = HoldoutManager(max_date=new_max, holdout_weeks=6)

        assert mgr_new.get_max_training_date() > mgr_old.get_max_training_date()

    def test_holdout_always_anchored_to_max_date(self):
        """Regardless of construction order, holdout end is always max_date."""
        dates = [
            datetime.date(2026, 1, 15),
            datetime.date(2026, 2, 28),
            datetime.date(2026, 3, 29),
        ]
        for d in dates:
            mgr = HoldoutManager(max_date=d, holdout_weeks=6)
            assert mgr.holdout_end_date == d
            assert mgr.holdout_start_date == d - datetime.timedelta(weeks=6)
