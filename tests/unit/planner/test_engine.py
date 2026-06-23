"""Tests for planner/engine.py (TDD)."""

from __future__ import annotations

import pytest

from factorydaemon.planner.engine import plan
from factorydaemon.storage.norms import NormStorage


def _norms() -> NormStorage:
    return NormStorage(
        {
            "cut": 120.0,
            "sew": 180.0,
            "pack": 60.0,
            "inspect": 30.0,
        }
    )


def test_plan_warns_on_missing_norms_but_completes():
    norms = _norms()
    result = plan(
        demands={"cut": 10, "unknown": 5},
        priorities={"cut": 1, "unknown": 1},
        norms=norms,
    )
    assert any("unknown" in w for w in result.warnings)
    # unknown should not appear in any worker load
    positions = {load.position for w in result.workers for load in w.loads}
    assert "unknown" not in positions


def test_plan_warns_on_zero_or_negative_demand():
    norms = _norms()
    result = plan(
        demands={"cut": 0, "sew": 1},
        priorities={"cut": 1, "sew": 1},
        norms=norms,
    )
    assert any("cut" in w and "0" in w for w in result.warnings)


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
        shift_hours=10,
        max_positions_per_worker=10,
    )
    assert result.worker_count == 1
    assert len(result.workers[0].loads) == 3
    assert result.utilization == pytest.approx(result.total_seconds / (10 * 3600), rel=1e-9)


def test_plan_respects_position_limit():
    norms = _norms()
    result = plan(
        demands={"cut": 1, "sew": 1, "pack": 1, "inspect": 1},
        priorities={"cut": 4, "sew": 3, "pack": 2, "inspect": 1},
        norms=norms,
        shift_hours=10,
        max_positions_per_worker=2,
    )
    assert all(w.positions_count <= 2 for w in result.workers)
    assert result.worker_count >= 2


def test_plan_min_workers_by_labour():
    norms = _norms()
    result = plan(
        demands={"cut": 100, "sew": 100},
        priorities={"cut": 1, "sew": 1},
        norms=norms,
        shift_hours=10,
        max_positions_per_worker=10,
    )
    assert result.worker_count == 1


def test_plan_chunks_large_position_spreads_across_workers():
    norms = NormStorage({"big": 1.0})
    result = plan(
        demands={"big": 72_000},
        priorities={"big": 1},
        norms=norms,
        shift_hours=10,
        max_positions_per_worker=5,
    )
    assert result.required_workers == 2
    totals = {}
    for w in result.workers:
        assert w.used_seconds <= 10 * 3600 + 1e-9
        for load in w.loads:
            totals[load.position] = totals.get(load.position, 0.0) + load.units
    assert totals.get("big", 0.0) == pytest.approx(72_000.0, rel=1e-9)


def test_plan_position_without_norm_excluded_not_blocked():
    norms = NormStorage({"has_norm": 60.0})
    result = plan(
        demands={"has_norm": 10, "no_norm": 5},
        priorities={"has_norm": 2, "no_norm": 1},
        norms=norms,
        shift_hours=10,
        max_positions_per_worker=5,
    )
    assert any("no_norm" in w for w in result.warnings)
    positions = {load.position for w in result.workers for load in w.loads}
    assert "no_norm" not in positions
    assert any(load.position == "has_norm" for w in result.workers for load in w.loads)


def test_plan_saves_target_workers():
    norms = _norms()
    result = plan(
        demands={"cut": 10},
        priorities={"cut": 1},
        norms=norms,
        shift_hours=10,
        max_positions_per_worker=5,
        target_workers=2,
    )
    assert result.target_workers == 2


def test_plan_forces_target_workers_even_on_overload():
    norms = NormStorage({"task": 3600.0})
    # 15 tasks * 3600 sec = 54_000 sec of work, shift = 36_000 sec, target=1 worker.
    # With auto-expansion we now raise target_workers to the required count.
    result = plan(
        demands={"task": 15},
        priorities={"task": 1},
        norms=norms,
        shift_hours=10,
        max_positions_per_worker=5,
        target_workers=1,
    )
    assert result.required_workers == 2
    assert result.worker_count == 2
    assert all(w.used_seconds <= 10 * 3600 + 1e-6 for w in result.workers)
    assert any("Автоматически увеличено" in w for w in result.warnings)


def test_plan_target_workers_with_insufficient_capacity():
    norms = NormStorage({"task": 3600.0})
    # 10 tasks fit exactly in one shift; user asks for 5 workers → pack into 1.
    result = plan(
        demands={"task": 10},
        priorities={"task": 1},
        norms=norms,
        shift_hours=10,
        max_positions_per_worker=5,
        target_workers=5,
    )
    assert result.required_workers == 1
    assert result.worker_count == 1
    assert result.utilization == 1.0
    assert any("свободная ёмкость для 4 работников" in w for w in result.warnings)


def test_plan_respects_requested_target_workers_even_when_underloaded():
    norms = NormStorage({"task": 3600.0})
    # 5 tasks fit in one worker, but user asks for 3 workers → still 1 worker.
    result = plan(
        demands={"task": 5},
        priorities={"task": 1},
        norms=norms,
        shift_hours=10,
        max_positions_per_worker=5,
        target_workers=3,
    )
    assert result.worker_count == 1
    assert result.required_workers == 1
    assert result.utilization == 0.5
    assert any("свободная ёмкость для 2 работников" in w for w in result.warnings)


def test_plan_ignores_demands_without_priority():
    norms = NormStorage({"task": 3600.0, "stock": 1800.0})
    result = plan(
        demands={"task": 5, "stock": 10},
        priorities={"task": 1},
        norms=norms,
        shift_hours=10,
        max_positions_per_worker=5,
    )
    # Only "task" is planned; "stock" has no priority and is ignored.
    assert result.worker_count == 1
    assigned = {load.position for w in result.workers for load in w.loads}
    assert assigned == {"task"}
    assert result.total_seconds == 5 * 3600.0


def test_plan_max_five_positions_per_worker():
    norms = NormStorage({f"pos{i}": 600.0 for i in range(10)})
    demands = {f"pos{i}": 1 for i in range(10)}
    priorities = {f"pos{i}": i for i in range(10)}
    result = plan(
        demands=demands,
        priorities=priorities,
        norms=norms,
        shift_hours=10,
        max_positions_per_worker=5,
    )
    assert all(w.positions_count <= 5 for w in result.workers)


def test_plan_splits_large_positions_across_workers():
    norms = NormStorage({"big": 1.0})
    result = plan(
        demands={"big": 72_000},
        priorities={"big": 1},
        norms=norms,
        shift_hours=10,
        max_positions_per_worker=5,
    )
    assert result.worker_count == 2
    assert all(w.positions_count == 1 for w in result.workers)


def test_plan_aggregates_chunks_by_position():
    norms = NormStorage({"big": 1.0})
    result = plan(
        demands={"big": 72_000},
        priorities={"big": 1},
        norms=norms,
        shift_hours=10,
        max_positions_per_worker=5,
    )
    assigned_units = {}
    for w in result.workers:
        for load in w.loads:
            assigned_units[load.position] = assigned_units.get(load.position, 0.0) + load.units
    assert assigned_units == {"big": pytest.approx(72_000.0, rel=1e-9)}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
