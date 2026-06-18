"""Tests for planner/engine.py (TDD)."""
from __future__ import annotations

import math

import pytest

from factorydaemon.planner.engine import plan
from factorydaemon.storage.norms import NormStorage


def _norms() -> NormStorage:
    return NormStorage({
        "cut": 120.0,      # 2 min per unit
        "sew": 180.0,      # 3 min per unit
        "pack": 60.0,      # 1 min per unit
        "inspect": 30.0,   # 30 sec per unit
    })


def test_plan_raises_on_missing_norms():
    norms = _norms()
    with pytest.raises(ValueError, match="Missing norms"):
        plan(
            demands={"cut": 10, "unknown": 5},
            priorities={"cut": 1, "unknown": 1},
            norms=norms,
        )


def test_plan_raises_on_zero_or_negative_demand():
    norms = _norms()
    with pytest.raises(ValueError, match="must be > 0"):
        plan(
            demands={"cut": 0},
            priorities={"cut": 1},
            norms=norms,
        )


def test_plan_raises_on_bad_shift_hours():
    norms = _norms()
    with pytest.raises(ValueError, match="shift_hours must be positive"):
        plan({"cut": 1}, {"cut": 1}, norms, shift_hours=0)


def test_plan_single_worker_fits_all():
    norms = _norms()
    result = plan(
        demands={"cut": 10, "sew": 5, "pack": 20},
        priorities={"cut": 3, "sew": 2, "pack": 1},
        norms=norms,
        shift_hours=8,
        max_positions_per_worker=10,
    )
    assert result.worker_count == 1
    assert len(result.workers[0].loads) == 3
    assert result.utilization == pytest.approx(
        result.total_seconds / (8 * 3600), rel=1e-9
    )


def test_plan_respects_position_limit():
    norms = _norms()
    # 4 positions, limit 2 per worker -> at least 2 workers.
    result = plan(
        demands={"cut": 1, "sew": 1, "pack": 1, "inspect": 1},
        priorities={"cut": 4, "sew": 3, "pack": 2, "inspect": 1},
        norms=norms,
        shift_hours=8,
        max_positions_per_worker=2,
    )
    assert all(w.positions_count <= 2 for w in result.workers)
    assert result.worker_count >= 2


def test_plan_min_workers_by_labour():
    norms = _norms()
    # Total labour = 100*120 + 100*180 = 30_000 sec = 1.04 shifts of 8h (28_800).
    # Minimal theoretical count is therefore 2 workers.
    result = plan(
        demands={"cut": 100, "sew": 100},
        priorities={"cut": 1, "sew": 1},
        norms=norms,
        shift_hours=8,
        max_positions_per_worker=10,
    )
    assert result.worker_count == 2
    total_assigned = sum(w.used_seconds for w in result.workers)
    assert total_assigned == pytest.approx(30_000.0, rel=1e-9)


def test_plan_priority_order_placed_first():
    norms = NormStorage({"a": 3600.0, "b": 3600.0, "c": 1.0})
    result = plan(
        demands={"a": 1, "b": 1, "c": 1},
        priorities={"a": 10, "b": 5, "c": 1},
        norms=norms,
        shift_hours=8,
        max_positions_per_worker=10,
    )
    positions = [l.position for l in result.workers[0].loads]
    # Highest priority should be first in the first worker's list.
    assert positions[0] == "a"


def test_plan_no_overload():
    norms = _norms()
    # Keep every individual position within one shift; 160 units of sew = 28_800 sec exactly.
    result = plan(
        demands={"cut": 160, "sew": 160, "pack": 200},
        priorities={"cut": 3, "sew": 2, "pack": 1},
        norms=norms,
        shift_hours=8,
        max_positions_per_worker=3,
    )
    for w in result.workers:
        assert w.used_seconds <= 8 * 3600 + 1e-9


def test_plan_backfill_reduces_worker_count():
    # Two big tasks, one leaves slack that fits the small task.
    norms = NormStorage({"big1": 3600.0, "big2": 3600.0, "small": 60.0})
    result = plan(
        demands={"big1": 8, "big2": 7, "small": 1},  # big2 uses 25_200 sec, slack 3_600
        priorities={"big1": 3, "big2": 2, "small": 1},
        norms=norms,
        shift_hours=8,
        max_positions_per_worker=10,
    )
    # With slack back-filling 2 workers are enough.
    assert result.worker_count == 2


def test_plan_total_seconds_matches_demand():
    norms = _norms()
    demands = {"cut": 15, "sew": 8, "pack": 25}
    expected = sum(demands[p] * norms.get(p).seconds_per_unit for p in demands)
    result = plan(
        demands=demands,
        priorities={"cut": 1, "sew": 1, "pack": 1},
        norms=norms,
    )
    assert result.total_seconds == pytest.approx(expected, rel=1e-9)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
