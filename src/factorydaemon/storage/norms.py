"""Stub storage for production norms.

Full SQLite implementation is VID-284. This module exposes the minimal
interface required by planner/engine.py so that VID-285 can be developed
and tested independently.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class Norm:
    position: str
    seconds_per_unit: float


class NormStorage:
    """In-memory norm storage with the same interface the SQLite version will use."""

    def __init__(self, norms: dict[str, float] | None = None) -> None:
        # position -> seconds per production unit
        self._data: dict[str, float] = dict(norms or {})

    def upsert(self, mapping: dict[str, float]) -> None:
        """Merge new/updated norms into storage."""
        self._data.update(mapping)

    def get(self, position: str) -> Norm | None:
        """Return Norm for a position or None if missing."""
        seconds = self._data.get(position)
        if seconds is None:
            return None
        return Norm(position=position, seconds_per_unit=float(seconds))

    def missing(self, positions: Iterable[str]) -> list[str]:
        """Return positions that do not have a norm yet."""
        return [p for p in positions if p not in self._data]

    def __contains__(self, position: str) -> bool:
        return position in self._data
