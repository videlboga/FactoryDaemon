"""User session state for FactoryDaemon bot conversations.

A session tracks all files and data uploaded during one planning conversation
and drives the multi-step pipeline:

1. Collect остатки (demands), нормы (norms), приоритеты (priorities).
2. Detect missing data and ask the user for clarification.
3. Run the planner and detect underload.
4. Optionally ask for additional priorities and re-plan.
5. Generate the final Excel report.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, cast

import pandas as pd

if TYPE_CHECKING:
    from factorydaemon.planner.engine import PlanResult


class Step(Enum):
    """Conversation step in a planning session."""

    COLLECTING = "collecting"
    MISSING_NORMS = "missing_norms"
    MISSING_PRIORITIES = "missing_priorities"
    READY_TO_PLAN = "ready_to_plan"
    ASKING_WORKERS = "asking_workers"
    UNDERLOAD = "underload"
    PLAN_READY = "plan_ready"
    FINISHED = "finished"


@dataclass
class UserSession:
    """Holds the full context of one user planning session."""

    session_id: str
    shift_hours: float = 8.0
    max_positions_per_worker: int = 3
    underload_threshold: float = 0.75

    # Raw parsed tables
    demands_df: pd.DataFrame | None = None
    norms_df: pd.DataFrame | None = None
    priorities_df: pd.DataFrame | None = None

    # Extracted/cleaned values (position -> value)
    demands: dict[str, float] = field(default_factory=dict)
    norms: dict[str, float] = field(default_factory=dict)
    priorities: dict[str, int] = field(default_factory=dict)

    # Planning results
    plan_result: PlanResult | None = None
    warnings: list[str] = field(default_factory=list)

    # Conversation state
    step: Step = Step.COLLECTING
    history: list[dict[str, str]] = field(default_factory=list)

    # Keep track of what we have already asked for to avoid loops
    asked_for_norms: bool = False
    asked_for_priorities: bool = False
    asked_for_more_priorities_underload: bool = False
    target_workers: int | None = None

    def add_message(self, role: str, text: str) -> None:
        """Append a conversation turn."""
        self.history.append({"role": role, "text": text})

    def update_demands(self, df: pd.DataFrame, position_col: str, quantity_col: str) -> None:
        """Store demand table and extract demands."""
        self.demands_df = df
        for _, row in df.iterrows():
            pos = str(row[position_col]).strip()
            qty = _to_float(row[quantity_col])
            if pos and qty is not None and qty > 0:
                self.demands[pos] = qty

    def update_norms(self, df: pd.DataFrame, position_col: str, time_col: str) -> None:
        """Store norms table and extract seconds per unit."""
        self.norms_df = df
        for _, row in df.iterrows():
            pos = str(row[position_col]).strip()
            sec = _to_float(row[time_col])
            if pos and sec is not None and sec > 0:
                self.norms[pos] = sec

    def update_priorities(
        self,
        df: pd.DataFrame,
        position_col: str,
        priority_col: str | None,
        *,
        use_row_order: bool = False,
    ) -> None:
        """Store priorities table and extract priorities.

        If use_row_order is True (or priority_col is None and use_row_order is True),
        the row order itself defines priority (top row = highest priority).
        Otherwise explicit numeric priority values are used.
        """
        self.priorities_df = df
        use_order = use_row_order or priority_col is None
        if not use_order and priority_col in df.columns:
            values = pd.to_numeric(df[priority_col], errors="coerce").dropna()
            if len(values) == 0:
                use_order = True

        n_rows = len(df)
        for idx, (_, row) in enumerate(df.iterrows()):
            pos = str(row[position_col]).strip()
            if not pos or pos.lower() == "nan":
                continue
            prio = n_rows - idx if use_order else _to_int(row[priority_col])
            if prio is not None:
                self.priorities[pos] = prio

    @property
    def missing_norms_positions(self) -> list[str]:
        """Demand positions with no known norm."""
        return [p for p in self.demands if p not in self.norms]

    @property
    def missing_priorities_positions(self) -> list[str]:
        """Demand positions with no known priority."""
        return [p for p in self.demands if p not in self.priorities]

    @property
    def is_ready_to_plan(self) -> bool:
        """True when demands, norms and priorities are complete."""
        return bool(
            self.demands
            and not self.missing_norms_positions
            and not self.missing_priorities_positions
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to plain dict for persistence."""
        return {
            "session_id": self.session_id,
            "shift_hours": self.shift_hours,
            "max_positions_per_worker": self.max_positions_per_worker,
            "underload_threshold": self.underload_threshold,
            "demands": dict(self.demands),
            "norms": dict(self.norms),
            "priorities": dict(self.priorities),
            "step": self.step.value,
            "warnings": list(self.warnings),
            "asked_for_norms": self.asked_for_norms,
            "asked_for_priorities": self.asked_for_priorities,
            "asked_for_more_priorities_underload": self.asked_for_more_priorities_underload,
            "history": [dict(h) for h in self.history],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UserSession:
        """Restore session from dict."""
        sess = cls(
            session_id=cast(str, data.get("session_id", "")),
            shift_hours=cast(float, data.get("shift_hours", 8.0)),
            max_positions_per_worker=cast(int, data.get("max_positions_per_worker", 3)),
            underload_threshold=cast(float, data.get("underload_threshold", 0.75)),
        )
        sess.demands = cast(dict[str, float], data.get("demands", {}))
        sess.norms = cast(dict[str, float], data.get("norms", {}))
        sess.priorities = cast(dict[str, int], data.get("priorities", {}))
        sess.step = Step(cast(str, data.get("step", "collecting")))
        sess.warnings = cast(list[str], data.get("warnings", []))
        sess.asked_for_norms = cast(bool, data.get("asked_for_norms", False))
        sess.asked_for_priorities = cast(bool, data.get("asked_for_priorities", False))
        sess.asked_for_more_priorities_underload = cast(
            bool, data.get("asked_for_more_priorities_underload", False)
        )
        sess.history = [dict(cast(dict[str, str], h)) for h in data.get("history", [])]
        return sess


def _to_float(value: object) -> float | None:
    """Coerce an arbitrary cell value to float."""
    if value is None:
        return None
    # Handle pandas/numpy NaN without stringifying to 'nan'.
    try:
        import math

        if isinstance(value, float) and math.isnan(value):
            return None
    except Exception:
        pass
    text = str(value).strip().replace(",", ".")
    if not text:
        return None
    try:
        result = float(text)
    except ValueError:
        return None
    import math

    if math.isnan(result):
        return None
    return result


def _to_int(value: object) -> int | None:
    """Coerce an arbitrary cell value to int."""
    f = _to_float(value)
    if f is None:
        return None
    return int(f)
