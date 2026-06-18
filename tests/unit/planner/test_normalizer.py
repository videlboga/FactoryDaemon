"""Tests for the position normalizer."""

import pytest

from factorydaemon.planner.normalizer import normalize_position


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Л43", "Л43"),
        ("л43", "Л43"),
        ("Л-43", "Л43"),
        ("Л 43", "Л43"),
        (" л 43 ", "Л43"),
        ("11В-11", "11В-11"),
        ("11в-11", "11В-11"),
        ("11В11", "11В-11"),
        (" 11в11 ", "11В-11"),
        ("10", "10"),
        ("Л1", "Л1"),
        ("Л-1", "Л1"),
        ("Л 1", "Л1"),
    ],
)
def test_normalize_position(raw: str, expected: str) -> None:
    """Common position spellings collapse to canonical form."""
    assert normalize_position(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["", "   ", " - ", None],
)
def test_normalize_position_rejects_invalid(raw: object) -> None:
    """Invalid or empty inputs raise ValueError."""
    with pytest.raises(ValueError):
        normalize_position(raw)  # type: ignore[arg-type]
