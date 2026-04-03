"""Holdout window management for experiment evaluation.

Computes and enforces a mandatory out-of-sample holdout period that is
structurally excluded from all training, validation, and feature engineering.
"""
import datetime
import logging
from typing import Optional

from gefion.observability import create_span

logger = logging.getLogger(__name__)


class HoldoutManager:
    """Manages the out-of-sample holdout window for experiment evaluation.

    The holdout is always the most recent N weeks of data. It is used exactly
    once — at final evaluation — and must be structurally excluded from all
    training, validation, and feature engineering data.
    """

    def __init__(
        self,
        max_date: datetime.date,
        holdout_weeks: int = 6,
        min_date: Optional[datetime.date] = None,
    ):
        if holdout_weeks <= 0:
            raise ValueError(f"holdout_weeks must be positive, got {holdout_weeks}")

        self.holdout_weeks = holdout_weeks
        self.holdout_end_date = max_date
        self.holdout_start_date = max_date - datetime.timedelta(weeks=holdout_weeks)

        # Validate minimum data span if min_date provided
        if min_date is not None:
            data_span = (max_date - min_date).days
            if data_span < 14:  # 2 weeks minimum
                raise ValueError(
                    f"Data span is {data_span} days — need at least 2 weeks "
                    f"(14 days) for holdout validation. "
                    f"min_date={min_date}, max_date={max_date}"
                )

    def get_max_training_date(self) -> datetime.date:
        """Return the last date available for training (one day before holdout starts)."""
        return self.holdout_start_date - datetime.timedelta(days=1)

    def to_dict(self) -> dict:
        """Serialize to a JSON-friendly dictionary."""
        return {
            "holdout_weeks": self.holdout_weeks,
            "holdout_start_date": str(self.holdout_start_date),
            "holdout_end_date": str(self.holdout_end_date),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "HoldoutManager":
        """Reconstruct a HoldoutManager from a serialized dictionary."""
        end_date = datetime.date.fromisoformat(d["holdout_end_date"])
        return cls(
            max_date=end_date,
            holdout_weeks=d["holdout_weeks"],
        )

    def __repr__(self) -> str:
        return (
            f"HoldoutManager(holdout_weeks={self.holdout_weeks}, "
            f"start={self.holdout_start_date}, end={self.holdout_end_date})"
        )
