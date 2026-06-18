"""Tests for planner/validator.py (TDD)."""
from __future__ import annotations

import pytest

from factorydaemon.planner.engine import plan
from factorydaemon.planner.validator import check_plan, validate_plan_inputs, validate_plan_result
from factorydaemon.storage.norms import NormStorage


def _norms() -> NormStorage:
    return NormStorage({
        "cut": 120.0,
        "sew": 180.0,
        "pack": 60.0,
    })


def test_validate_inputs_missing_norms():
    errors = validate_plan_inputs(
        demands={"cut": 10, "unknown": 5},
        priorities={"cut": 1, "unknown": 1},
        norms=_norms(),
    )
    assert any(e.code == "MISSING_NORMS" for e in errors)


def test_validate_inputs_zero_volume():
    errors = validate_plan_inputs(
        demands={"cut": 0},
        priorities={"cut": 1},
        norms=_norms(),
    )
    assert any(e.code == "INVALID_VOLUME" for e in errors)


def test_validate_inputs_missing_priority():
    errors = validate_plan_inputs(
        demands={"cut": 10},
        priorities={},
        norms=_norms(),
    )
    assert any(e.code == "MISSING_PRIORITY" for e in errors)


def test_validate_inputs_no_demand():
    errors = validate_plan_inputs({}, {}, _norms())
    assert any(e.code == "NO_DEMAND" for e in errors)


def test_validate_result_position_limit():
    # Manually construct a worker with too many positions to test the limit rule.
    from factorydaemon.planner.engine import PlanResult, PositionLoad, Worker

    result = PlanResult(
        workers=[
            Worker(
                index=0,
                capacity_seconds=28800.0,
                loads=[
                    PositionLoad("cut", 1.0, 120.0, 120.0),
                    PositionLoad("sew", 1.0, 180.0, 180.0),
                ],
            )
        ],
        shift_seconds=28800.0,
        max_positions_per_worker=1,
        total_seconds=300.0,
    )
    errors = validate_plan_result(result, {"cut": 1, "sew": 1}, shift_hours=8)
    assert any(e.code == "POSITION_LIMIT" for e in errors)


def test_validate_result_overload():
    # Manually construct an overloaded worker to test the overload rule.
    from factorydaemon.planner.engine import PlanResult, PositionLoad, Worker

    result = PlanResult(
        workers=[
            Worker(
                index=0,
                capacity_seconds=28800.0,
                loads=[
                    PositionLoad("cut", 300.0, 120.0, 300.0 * 120.0),
                ],
            )
        ],
        shift_seconds=28800.0,
        max_positions_per_worker=3,
        total_seconds=300.0 * 120.0,
    )
    errors = validate_plan_result(result, {"cut": 300}, shift_hours=8)
    assert any(e.code == "OVERLOAD" for e in errors)


def test_validate_result_missing_in_plan():
    norms = _norms()
    result = plan(
        demands={"cut": 1},
        priorities={"cut": 1},
        norms=norms,
    )
    errors = validate_plan_result(result, {"cut": 1, "sew": 1}, shift_hours=8)
    assert any(e.code == "MISSING_IN_PLAN" for e in errors)


def test_check_plan_passes_for_valid_plan():
    norms = _norms()
    demands = {"cut": 10, "sew": 5, "pack": 20}
    priorities = {"cut": 3, "sew": 2, "pack": 1}
    result = plan(demands=demands, priorities=priorities, norms=norms, max_positions_per_worker=10)
    errors = check_plan(demands, priorities, norms, result)
    assert errors == []


def test_check_plan_fails_when_input_bad():
    norms = _norms()
    demands = {"cut": 0}
    priorities = {"cut": 1}
    # Even a dummy result can't fix bad input.
    result = plan({"cut": 1}, {"cut": 1}, norms)
    errors = check_plan(demands, priorities, norms, result)
    assert any(e.code == "INVALID_VOLUME" for e in errors)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
