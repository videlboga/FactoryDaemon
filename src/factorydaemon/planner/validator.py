"""Validators for a shift plan produced by planner/engine.py."""

from __future__ import annotations

from dataclasses import dataclass

from factorydaemon.planner.engine import PlanResult
from factorydaemon.storage.norms import NormStorage


@dataclass(frozen=True)
class ValidationError:
    code: str
    message: str


def _approx_equal(a: float, b: float, rel: float = 1e-9, abs_tol: float = 1e-9) -> bool:
    """Compare two floats with a small tolerance."""
    return abs(a - b) <= max(rel * max(abs(a), abs(b)), abs_tol)


def validate_plan_result(
    plan_result: PlanResult,
    demands: dict[str, float],
    shift_hours: float,
) -> list[ValidationError]:
    """Validate a produced plan against business rules."""
    errors: list[ValidationError] = []
    shift_seconds = shift_hours * 3600.0

    if not plan_result.workers:
        errors.append(ValidationError("NO_WORKERS", "Plan has no workers."))
        return errors

    assigned_positions: set[str] = set()
    assigned_units: dict[str, float] = {}
    for worker in plan_result.workers:
        if worker.positions_count > plan_result.max_positions_per_worker:
            errors.append(
                ValidationError(
                    "POSITION_LIMIT",
                    f"Worker {worker.index} has {worker.positions_count} positions, "
                    f"limit is {plan_result.max_positions_per_worker}.",
                )
            )
        if worker.used_seconds > shift_seconds + 1e-9:
            errors.append(
                ValidationError(
                    "OVERLOAD",
                    f"Worker {worker.index} is loaded {worker.used_seconds:.0f}s "
                    f"which exceeds shift {shift_seconds:.0f}s.",
                )
            )
        for load in worker.loads:
            assigned_positions.add(load.position)
            assigned_units[load.position] = assigned_units.get(load.position, 0.0) + load.units

    for position, required_units in demands.items():
        if position not in assigned_positions:
            # Positions without norms are excluded from the plan legitimately.
            continue
        elif not _approx_equal(assigned_units.get(position, 0.0), required_units):
            errors.append(
                ValidationError(
                    "PARTIAL_VOLUME",
                    f"Position {position!r} assigned {assigned_units.get(position, 0.0)} "
                    f"units, expected {required_units}.",
                )
            )

    return errors


def validate_plan_inputs(
    demands: dict[str, float],
    priorities: dict[str, int],
    norms: NormStorage,
) -> list[ValidationError]:
    """Validate raw inputs before planning."""
    errors: list[ValidationError] = []

    if not demands:
        errors.append(ValidationError("NO_DEMAND", "No production demand provided."))
        return errors

    for position, units in demands.items():
        if units <= 0:
            errors.append(
                ValidationError(
                    "INVALID_VOLUME",
                    f"Volume for {position!r} must be > 0, got {units}.",
                )
            )

    return errors


def check_plan(
    demands: dict[str, float],
    priorities: dict[str, int],
    norms: NormStorage,
    plan_result: PlanResult,
    shift_hours: float = 8.0,
) -> list[ValidationError]:
    """Run input validation and result validation together."""
    errors: list[ValidationError] = []
    input_errors = validate_plan_inputs(demands, priorities, norms)
    if input_errors:
        errors.extend(input_errors)
        return errors
    result_errors = validate_plan_result(plan_result, demands, shift_hours)
    errors.extend(result_errors)
    return errors
