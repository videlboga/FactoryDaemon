"""User session state for FactoryDaemon bot conversations."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, cast

import pandas as pd

from factorydaemon.planner.normalizer import normalize_position

if TYPE_CHECKING:
    from factorydaemon.planner.engine import PlanResult


class Step(Enum):
    COLLECTING = "collecting"
    MISSING_NORMS = "missing_norms"
    MISSING_PRIORITIES = "missing_priorities"
    ASKING_WORKERS = "asking_workers"
    READY_TO_PLAN = "ready_to_plan"
    UNDERLOAD = "underload"
    PLAN_READY = "plan_ready"
    FINISHED = "finished"


@dataclass
class UserSession:
    session_id: str
    shift_hours: float = 10.0
    max_positions_per_worker: int = 5
    underload_threshold: float = 0.95
    target_workers: int | None = None

    demands_df: pd.DataFrame | None = None
    norms_df: pd.DataFrame | None = None
    priorities_df: pd.DataFrame | None = None

    # Stock (остатки) uploaded as the first file.
    demands: dict[str, float] = field(default_factory=dict)
    # Planned quantities from the third (plan) file. Stored separately so the
    # planner can cap demand by available stock.
    plan_quantities: dict[str, float] = field(default_factory=dict)
    norms: dict[str, float] = field(default_factory=dict)
    priorities: dict[str, int] = field(default_factory=dict)
    extra_priorities: dict[str, int] = field(default_factory=dict)

    plan_result: PlanResult | None = None
    warnings: list[str] = field(default_factory=list)

    step: Step = Step.COLLECTING
    history: list[dict[str, str]] = field(default_factory=list)

    asked_for_norms: bool = False
    asked_for_priorities: bool = False
    asked_for_more_priorities_underload: bool = False

    @property
    def missing_norms_positions(self) -> list[str]:
        return [p for p in self.demands if p not in self.norms]

    @property
    def missing_priorities_positions(self) -> list[str]:
        return [p for p in self.effective_demands() if p not in self.priorities]

    @property
    def is_ready_to_plan(self) -> bool:
        return bool(self.effective_demands() and self.norms and self.priorities)

    def add_message(self, role: str, text: str) -> None:
        self.history.append({"role": role, "text": text})

    def update_demands(self, df: pd.DataFrame, position_col: str, quantity_col: str) -> None:
        self.demands_df = df
        for _, row in df.iterrows():
            raw = str(row[position_col]).strip()
            if not raw or raw.lower() == "nan":
                continue
            pos = normalize_position(raw)
            qty = _to_float(row[quantity_col])
            if qty is not None and qty > 0:
                self.demands[pos] = self.demands.get(pos, 0.0) + qty

    def update_norms(self, df: pd.DataFrame, position_col: str, time_col: str) -> None:
        self.norms_df = df
        for _, row in df.iterrows():
            raw = str(row[position_col]).strip()
            if not raw or raw.lower() == "nan":
                continue
            pos = normalize_position(raw)
            sec = _to_float(row[time_col])
            if sec is not None and sec > 0:
                self.norms[pos] = sec

    def update_priorities(
        self,
        df: pd.DataFrame,
        position_col: str,
        priority_col: str | None,
        *,
        use_row_order: bool = False,
        extra: bool = False,
        is_plan_file: bool = False,
    ) -> None:
        """Store priorities for positions.

        When ``is_plan_file`` is True, the second column is treated as the planned
        quantity (demand) and priorities are derived from row order. Otherwise the
        second column is treated as an explicit priority value.
        """
        target = self.extra_priorities if extra else self.priorities
        if not extra:
            self.priorities_df = df

        if is_plan_file:
            use_order = True
        else:
            use_order = use_row_order or priority_col is None
            if not use_order and priority_col in df.columns:
                values = pd.to_numeric(df[priority_col], errors="coerce").dropna()
                if len(values) == 0:
                    use_order = True

        n_rows = len(df)
        for idx, (_, row) in enumerate(df.iterrows()):
            raw = str(row[position_col]).strip()
            if not raw or raw.lower() == "nan":
                continue
            pos = normalize_position(raw)

            if is_plan_file and priority_col is not None and priority_col in df.columns:
                qty = _to_float(row[priority_col])
                if qty is not None and qty > 0:
                    self.plan_quantities[pos] = self.plan_quantities.get(pos, 0.0) + qty

            if use_order:
                prio = n_rows - idx
            else:
                prio = _to_int(row[priority_col])
            if prio is not None:
                target[pos] = prio

    def add_extra_priorities(self, df: pd.DataFrame, position_col: str, priority_col: str | None = None) -> None:
        """Add priorities for additional positions (stock backfill)."""
        self.update_priorities(df, position_col, priority_col, extra=True)

    def effective_demands(self) -> dict[str, float]:
        """Return demand capped by available stock.

        Only positions that have a priority are planned. For each such position
        the effective quantity is the smaller of the planned quantity and the
        available stock. If no plan quantity was provided for a position, the
        full stock is used.
        """
        effective: dict[str, float] = {}
        for pos in self.priorities:
            stock = self.demands.get(pos, 0.0)
            plan = self.plan_quantities.get(pos)
            if plan is not None:
                effective[pos] = min(stock, plan)
            else:
                effective[pos] = stock
        return {pos: qty for pos, qty in effective.items() if qty > 0}

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "shift_hours": self.shift_hours,
            "max_positions_per_worker": self.max_positions_per_worker,
            "underload_threshold": self.underload_threshold,
            "target_workers": self.target_workers,
            "demands": dict(self.demands),
            "plan_quantities": dict(self.plan_quantities),
            "norms": dict(self.norms),
            "priorities": dict(self.priorities),
            "extra_priorities": dict(self.extra_priorities),
            "step": self.step.value,
            "warnings": list(self.warnings),
            "asked_for_norms": self.asked_for_norms,
            "asked_for_priorities": self.asked_for_priorities,
            "asked_for_more_priorities_underload": self.asked_for_more_priorities_underload,
            "history": [dict(h) for h in self.history],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UserSession:
        sess = cls(
            session_id=cast(str, data.get("session_id", "")),
            shift_hours=cast(float, data.get("shift_hours", 10.0)),
            max_positions_per_worker=cast(int, data.get("max_positions_per_worker", 5)),
            underload_threshold=cast(float, data.get("underload_threshold", 0.95)),
            target_workers=cast(int | None, data.get("target_workers", None)),
        )
        sess.demands = cast(dict[str, float], data.get("demands", {}))
        sess.plan_quantities = cast(dict[str, float], data.get("plan_quantities", {}))
        sess.norms = cast(dict[str, float], data.get("norms", {}))
        sess.priorities = cast(dict[str, int], data.get("priorities", {}))
        sess.extra_priorities = cast(dict[str, int], data.get("extra_priorities", {}))
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
    if value is None:
        return None
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
    f = _to_float(value)
    if f is None:
        return None
    return int(f)
