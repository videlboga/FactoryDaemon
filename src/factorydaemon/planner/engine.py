"""Production shift planner (bin-packing with splittable items)."""

from __future__ import annotations

from dataclasses import dataclass, field
from math import ceil, floor

from factorydaemon.storage.norms import NormStorage


@dataclass(frozen=True)
class PositionLoad:
    position: str
    units: float
    seconds_per_unit: float
    total_seconds: float
    source: str = "required"


@dataclass
class Worker:
    index: int
    capacity_seconds: float
    loads: list[PositionLoad] = field(default_factory=list)

    @property
    def used_seconds(self) -> float:
        return sum(load.total_seconds for load in self.loads)

    @property
    def remaining_seconds(self) -> float:
        return max(0.0, self.capacity_seconds - self.used_seconds)

    @property
    def positions_count(self) -> int:
        return len({load.position for load in self.loads})

    def can_accept_position(self, position: str, max_positions: int) -> bool:
        positions = {load.position for load in self.loads}
        return position in positions or len(positions) < max_positions

    def has_position(self, position: str) -> bool:
        return any(load.position == position for load in self.loads)

    def add(self, load: PositionLoad) -> None:
        self.loads.append(load)


@dataclass
class PlanResult:
    workers: list[Worker]
    shift_seconds: float
    max_positions_per_worker: int
    total_seconds: float
    unassigned: list[PositionLoad] = field(default_factory=list)
    target_workers: int | None = None
    required_workers: int | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def worker_count(self) -> int:
        return len(self.workers)

    @property
    def utilization(self) -> float:
        if not self.workers or self.shift_seconds <= 0:
            return 0.0
        return self.total_seconds / (len(self.workers) * self.shift_seconds)


@dataclass(frozen=True)
class InputError:
    message: str


def _build_loads(
    demands: dict[str, float],
    priorities: dict[str, int],
    norms: NormStorage,
) -> tuple[list[PositionLoad], list[str]]:
    """Build position loads only for demanded positions that have a norm."""
    loads: list[PositionLoad] = []
    warnings: list[str] = []

    # Sort by priority descending (highest first); tie-break by position name for stability.
    sorted_positions = sorted(
        priorities.keys(),
        key=lambda pos: (-priorities.get(pos, 0), pos),
    )

    for position in sorted_positions:
        quantity = demands.get(position, 0.0)
        if quantity <= 0:
            warnings.append(
                f"Позиция '{position}' имеет нулевой спрос ({quantity:.0f} ед.) — исключена из плана"
            )
            continue

        norm = norms.get(position)
        if norm is None or norm.seconds_per_unit <= 0:
            warnings.append(
                f"Нет нормы (сек/шт) для позиции '{position}' — исключена из плана"
            )
            continue

        seconds_per_unit = norm.seconds_per_unit
        total_seconds = quantity * seconds_per_unit
        loads.append(
            PositionLoad(
                position=position,
                units=float(quantity),
                seconds_per_unit=seconds_per_unit,
                total_seconds=total_seconds,
                source="required",
            )
        )

    return loads, warnings


def _assign_loads(
    loads: list[PositionLoad],
    shift_seconds: float,
    max_positions: int,
) -> list[Worker]:
    """Assign splittable loads to workers with tight bin-packing.

    Each worker is a bin with capacity ``shift_seconds`` and at most
    ``max_positions`` different positions. Large loads are split across bins;
    smaller loads fill gaps using best-fit decreasing.
    """
    workers: list[Worker] = []
    eps = 1e-9

    # Largest items first (FFD) so big loads anchor bins and small ones fill gaps.
    sorted_loads = sorted(loads, key=lambda load: -load.total_seconds)

    for load in sorted_loads:
        remaining_seconds = load.total_seconds
        remaining_units = load.units
        sec_per_unit = load.seconds_per_unit

        # First, try to add to workers that already contain this position
        # (continuing a split) as long as they have free time.
        for worker in workers:
            if remaining_seconds <= eps:
                break
            if not worker.has_position(load.position):
                continue
            free = worker.remaining_seconds
            if free <= eps:
                continue
            chunk_seconds = min(remaining_seconds, free)
            chunk_units = min(chunk_seconds / sec_per_unit, remaining_units)
            worker.add(
                PositionLoad(
                    position=load.position,
                    units=chunk_units,
                    seconds_per_unit=sec_per_unit,
                    total_seconds=chunk_seconds,
                    source=load.source,
                )
            )
            remaining_seconds -= chunk_seconds
            remaining_units -= chunk_units

        # Then place remaining units into workers that can accept a new position.
        while remaining_seconds > eps:
            best_worker: Worker | None = None
            best_remaining = float("inf")

            for worker in workers:
                free = worker.remaining_seconds
                if free <= eps:
                    continue
                if not worker.can_accept_position(load.position, max_positions):
                    continue
                # Best fit: smallest free space that still fits the chunk.
                if remaining_seconds <= free + eps and free < best_remaining:
                    best_worker = worker
                    best_remaining = free

            if best_worker is None:
                # Try any worker with free space if no worker can fit whole remainder.
                for worker in workers:
                    free = worker.remaining_seconds
                    if free <= eps:
                        continue
                    if not worker.can_accept_position(load.position, max_positions):
                        continue
                    if free > best_remaining:
                        best_worker = worker
                        best_remaining = free

            if best_worker is None:
                best_worker = Worker(index=len(workers), capacity_seconds=shift_seconds)
                workers.append(best_worker)

            chunk_seconds = min(remaining_seconds, best_worker.remaining_seconds)
            chunk_units = min(chunk_seconds / sec_per_unit, remaining_units)
            if chunk_units > remaining_units:
                chunk_units = remaining_units

            best_worker.add(
                PositionLoad(
                    position=load.position,
                    units=chunk_units,
                    seconds_per_unit=sec_per_unit,
                    total_seconds=chunk_seconds,
                    source=load.source,
                )
            )
            remaining_seconds -= chunk_seconds
            remaining_units -= chunk_units

    return workers


def _merge_chunks_in_workers(workers: list[Worker]) -> list[Worker]:
    """Merge multiple chunks of the same position inside a single worker."""
    for worker in workers:
        merged: dict[str, PositionLoad] = {}
        for load in worker.loads:
            if load.position in merged:
                existing = merged[load.position]
                merged[load.position] = PositionLoad(
                    position=load.position,
                    units=existing.units + load.units,
                    seconds_per_unit=load.seconds_per_unit,
                    total_seconds=existing.total_seconds + load.total_seconds,
                    source=load.source,
                )
            else:
                merged[load.position] = load
        worker.loads = list(merged.values())
    return workers


def _repack_workers(
    workers: list[Worker],
    shift_seconds: float,
    max_positions: int,
) -> list[Worker]:
    """Re-pack loads from existing workers to reduce worker count.

    After the initial greedy pass some workers may be underfilled because a
    large load forced an early split. This pass re-creates workers from all
    loads using best-fit decreasing to obtain a tighter packing.
    """
    all_loads: list[PositionLoad] = []
    for worker in workers:
        all_loads.extend(worker.loads)

    # Re-run the greedy assignment on the already-split loads.
    return _assign_loads(all_loads, shift_seconds, max_positions)


def _compact_workers(
    workers: list[Worker],
    shift_seconds: float,
    max_positions: int,
) -> list[Worker]:
    """Move loads from underfilled workers to better-filled ones if possible.

    This reduces fragmentation caused by the 5-position limit: if a worker has
    a small load and another worker has the same position with spare capacity,
    merge them.
    """
    eps = 1e-9
    changed = True
    while changed:
        changed = False
        for src_idx in range(len(workers) - 1, -1, -1):
            src = workers[src_idx]
            if src.used_seconds <= eps:
                # Empty worker can be removed.
                workers.pop(src_idx)
                changed = True
                continue

            for load in list(src.loads):
                # Find a destination worker (other than src) that already has
                # this position and enough free time.
                moved = False
                for dst in workers:
                    if dst is src:
                        continue
                    if not dst.has_position(load.position):
                        continue
                    if dst.remaining_seconds + eps < load.total_seconds:
                        continue
                    # Move the whole load to dst.
                    dst.add(load)
                    src.loads.remove(load)
                    moved = True
                    changed = True
                    break
                if moved and src.used_seconds <= eps:
                    break

            if src.used_seconds <= eps:
                workers.pop(src_idx)
                changed = True

    return workers


def _reindex_workers(workers: list[Worker]) -> list[Worker]:
    for idx, worker in enumerate(workers):
        worker.index = idx
    return workers


def plan(
    demands: dict[str, float],
    priorities: dict[str, int],
    norms: NormStorage,
    shift_hours: float = 10.0,
    max_positions_per_worker: int = 5,
    target_workers: int | None = None,
) -> PlanResult:
    if shift_hours <= 0:
        raise ValueError("shift_hours must be positive")
    if max_positions_per_worker <= 0:
        raise ValueError("max_positions_per_worker must be positive")

    shift_seconds = shift_hours * 3600.0
    loads, warnings = _build_loads(demands, priorities, norms)
    total_seconds = sum(load.total_seconds for load in loads)

    if total_seconds == 0:
        return PlanResult(
            workers=[Worker(index=0, capacity_seconds=shift_seconds)],
            shift_seconds=shift_seconds,
            max_positions_per_worker=max_positions_per_worker,
            total_seconds=0.0,
            target_workers=target_workers or 1,
            required_workers=1,
            warnings=warnings,
        )

    workers = _assign_loads(loads, shift_seconds, max_positions_per_worker)
    workers = _merge_chunks_in_workers(workers)
    workers = _repack_workers(workers, shift_seconds, max_positions_per_worker)
    workers = _merge_chunks_in_workers(workers)
    workers = _compact_workers(workers, shift_seconds, max_positions_per_worker)
    workers = _merge_chunks_in_workers(workers)
    workers = _reindex_workers(workers)

    actual_workers = len(workers)
    positions_in_plan = len({load.position for load in loads})

    required_by_time = max(1, ceil(total_seconds / shift_seconds))
    required_by_positions = max(1, ceil(positions_in_plan / max_positions_per_worker))
    required_workers = max(required_by_time, required_by_positions)

    if target_workers is not None and target_workers < actual_workers:
        warnings.append(
            f"Указано {target_workers} работников, но план требует {actual_workers}. "
            f"Автоматически увеличено до {actual_workers}."
        )

    if target_workers is not None and target_workers > actual_workers:
        free_workers = target_workers - actual_workers
        warnings.append(
            f"План размещён на {actual_workers} работниках. Запрошено {target_workers}: "
            f"свободная ёмкость для {free_workers} работников. "
            f"Пришлите дополнительные приоритеты/остатки для дозагрузки."
        )

    # Final safety checks.
    for worker in workers:
        if worker.positions_count > max_positions_per_worker:
            warnings.append(
                f"Работник {worker.index + 1} получил {worker.positions_count} позиций, "
                f"лимит {max_positions_per_worker}."
            )
        if worker.used_seconds > shift_seconds + 1e-6:
            warnings.append(
                f"Работник {worker.index + 1} перегружен: "
                f"{worker.used_seconds / 3600:.2f} ч при смене {shift_hours} ч."
            )

    return PlanResult(
        workers=workers,
        shift_seconds=shift_seconds,
        max_positions_per_worker=max_positions_per_worker,
        total_seconds=total_seconds,
        target_workers=target_workers,
        required_workers=required_workers,
        warnings=warnings,
    )


def plan_shift(
    demands: dict[str, float],
    priorities: dict[str, int],
    norms: NormStorage,
    shift_hours: float = 10.0,
    max_positions_per_worker: int = 5,
    target_workers: int | None = None,
) -> PlanResult:
    return plan(
        demands=demands,
        priorities=priorities,
        norms=norms,
        shift_hours=shift_hours,
        max_positions_per_worker=max_positions_per_worker,
        target_workers=target_workers,
    )


# Deprecated compatibility alias.
def calculate_plan(
    demands: dict[str, float],
    priorities: dict[str, int],
    norms: NormStorage,
    shift_hours: float = 10.0,
    max_positions_per_worker: int = 5,
) -> PlanResult:
    return plan(
        demands=demands,
        priorities=priorities,
        norms=norms,
        shift_hours=shift_hours,
        max_positions_per_worker=max_positions_per_worker,
    )


def validate_plan_inputs(
    demands: dict[str, float],
    priorities: dict[str, int],
    norms: NormStorage,
) -> list[InputError]:
    """Return a list of input errors that would prevent planning."""
    errors: list[InputError] = []
    if not demands:
        errors.append(InputError("Не загружены остатки/позиции (demands)."))
    if not priorities:
        errors.append(InputError("Не загружены приоритеты/план (priorities)."))
    if not norms:
        errors.append(InputError("Не загружены нормы времени (norms)."))

    missing_norms = [p for p in demands if p not in norms]
    if missing_norms:
        sample = ", ".join(missing_norms[:10])
        suffix = f" и ещё {len(missing_norms) - 10}" if len(missing_norms) > 10 else ""
        errors.append(InputError(f"Для позиций нет норм: {sample}{suffix}."))

    missing_priorities = [p for p in demands if p not in priorities]
    if missing_priorities:
        sample = ", ".join(missing_priorities[:10])
        suffix = f" и ещё {len(missing_priorities) - 10}" if len(missing_priorities) > 10 else ""
        errors.append(InputError(f"Для позиций нет приоритетов: {sample}{suffix}."))

    return errors
