"""Production shift planner (bin-packing).

The planner distributes production positions across workers for one shift.
Algorithm:
1. Compute labour per position = units * seconds_per_unit.
2. Compute minimal worker count = ceil(total labour / shift_seconds).
3. First Fit Decreasing (FFD) bin packing with a per-worker POSITION limit.
4. Back-fill under-loaded workers using priority order while respecting limits.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from factorydaemon.storage.norms import NormStorage


@dataclass(frozen=True)
class PositionLoad:
    position: str
    units: float
    seconds_per_unit: float
    total_seconds: float


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
        return self.capacity_seconds - self.used_seconds

    @property
    def positions_count(self) -> int:
        return len(self.loads)

    def can_fit(self, load: PositionLoad, max_positions: int) -> bool:
        return self.positions_count < max_positions and self.remaining_seconds >= load.total_seconds

    def add(self, load: PositionLoad) -> None:
        self.loads.append(load)


@dataclass
class PlanResult:
    workers: list[Worker]
    shift_seconds: float
    max_positions_per_worker: int
    total_seconds: float
    unassigned: list[PositionLoad] = field(default_factory=list)

    @property
    def worker_count(self) -> int:
        return len(self.workers)

    @property
    def utilization(self) -> float:
        if not self.workers or self.shift_seconds == 0:
            return 0.0
        return self.total_seconds / (self.worker_count * self.shift_seconds)


def _build_loads(
    demands: dict[str, float],
    priorities: dict[str, int],
    norms: NormStorage,
) -> list[PositionLoad]:
    """Validate norms and build PositionLoad records sorted by priority then labour."""
    missing = norms.missing(demands.keys())
    if missing:
        raise ValueError(f"Missing norms for positions: {missing}")

    loads: list[PositionLoad] = []
    for position, units in demands.items():
        if units <= 0:
            raise ValueError(f"Demand for {position!r} must be > 0, got {units}")
        norm = norms.get(position)
        if norm is None:
            raise ValueError(f"Missing norm for {position!r}")
        sec = float(units) * norm.seconds_per_unit
        loads.append(
            PositionLoad(
                position=position,
                units=float(units),
                seconds_per_unit=norm.seconds_per_unit,
                total_seconds=sec,
            )
        )

    # Sort: higher priority first, then larger labour first (FFD).
    loads.sort(key=lambda load: (-priorities.get(load.position, 0), -load.total_seconds))
    return loads


def plan(
    demands: dict[str, float],
    priorities: dict[str, int],
    norms: NormStorage,
    shift_hours: float = 8.0,
    max_positions_per_worker: int = 3,
) -> PlanResult:
    """Create a shift plan.

    Args:
        demands: position -> production units required.
        priorities: position -> integer priority (higher = more important).
        norms: NormStorage with seconds per unit for every demanded position.
        shift_hours: length of one shift in hours.
        max_positions_per_worker: hard limit of distinct positions per worker.

    Returns:
        PlanResult with assigned workers and any unassigned loads.
    """
    if shift_hours <= 0:
        raise ValueError("shift_hours must be positive")
    if max_positions_per_worker <= 0:
        raise ValueError("max_positions_per_worker must be positive")

    shift_seconds = shift_hours * 3600.0
    loads = _build_loads(demands, priorities, norms)
    total_seconds = sum(load.total_seconds for load in loads)

    if total_seconds == 0:
        return PlanResult(
            workers=[Worker(index=0, capacity_seconds=shift_seconds)],
            shift_seconds=shift_seconds,
            max_positions_per_worker=max_positions_per_worker,
            total_seconds=0.0,
        )

    min_workers = max(
        1, int(total_seconds // shift_seconds) + (1 if total_seconds % shift_seconds else 0)
    )

    # Phase 1: First Fit Decreasing placement into open bins.
    workers: list[Worker] = []
    for load in loads:
        placed = False
        for worker in workers:
            if worker.can_fit(load, max_positions_per_worker):
                worker.add(load)
                placed = True
                break
        if not placed:
            worker = Worker(index=len(workers), capacity_seconds=shift_seconds)
            worker.add(load)
            workers.append(worker)

    # Phase 2: compact to the minimal number of workers if FFD over-opened bins.
    if len(workers) > min_workers:
        workers = _repack(workers, min_workers, max_positions_per_worker, shift_seconds)

    # Phase 3: back-fill under-loaded workers using remaining slack.
    _backfill(workers, max_positions_per_worker)

    # Re-sort worker loads by priority for stable output.
    for w in workers:
        w.loads.sort(key=lambda load: -priorities.get(load.position, 0))

    return PlanResult(
        workers=workers,
        shift_seconds=shift_seconds,
        max_positions_per_worker=max_positions_per_worker,
        total_seconds=total_seconds,
    )


def _repack(
    workers: list[Worker],
    target_count: int,
    max_positions: int,
    shift_seconds: float,
) -> list[Worker]:
    """Try to move loads from the emptiest workers into earlier ones."""
    # Sort workers by used time ascending so emptiest are last.
    workers.sort(key=lambda w: w.used_seconds, reverse=True)

    new_workers: list[Worker] = []
    unassigned: list[PositionLoad] = []

    for load in [w_load for w in workers for w_load in w.loads]:
        placed = False
        for worker in new_workers:
            if worker.can_fit(load, max_positions):
                worker.add(load)
                placed = True
                break
        if not placed:
            if len(new_workers) < target_count:
                w = Worker(index=len(new_workers), capacity_seconds=shift_seconds)
                w.add(load)
                new_workers.append(w)
            else:
                unassigned.append(load)

    if unassigned:
        # Fall back to original allocation if repack failed.
        workers.sort(key=lambda w: w.index)
        return workers

    return new_workers


def _backfill(workers: list[Worker], max_positions: int) -> None:
    """Move small loads from heavily loaded workers to under-loaded ones.

    The goal is to reduce the total worker count while keeping every worker
    within capacity and under the position limit.
    """
    for donor in sorted(workers, key=lambda w: -w.used_seconds):
        for load in list(donor.loads):
            if len(donor.loads) <= 1 and len(workers) > 1:
                break
            # Try to fit into the least-loaded worker first.
            for target in sorted(workers, key=lambda w: w.used_seconds):
                if target is donor:
                    continue
                if target.can_fit(load, max_positions):
                    donor.loads.remove(load)
                    target.add(load)
                    break
