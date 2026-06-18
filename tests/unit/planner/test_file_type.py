"""Tests for file type detection."""

from __future__ import annotations

import pandas as pd
import pytest

from factorydaemon.planner.file_type import FileTypeResult, detect_file_type


def make_df(columns: dict[str, list]) -> pd.DataFrame:
    """Build a pandas DataFrame from a column dictionary."""
    return pd.DataFrame(columns)


@pytest.mark.parametrize(
    ("columns", "expected_type", "min_confidence"),
    [
        ({"Номенклатура": ["10", "Л43"], "Количество": [1850, 1200]}, "остатки", 0.9),
        ({"Деталь": ["10", "Л43"], "Сек/шт": [20, 10]}, "нормы", 0.9),
        ({"Позиция": ["Л43", "Л1", "10"], "Приоритет": [1, 2, 3]}, "приоритеты", 0.9),
        ({"position": ["Л43"], "quantity": [1200]}, "остатки", 0.9),
        ({"item": ["10"], "seconds_per_unit": [20]}, "нормы", 0.9),
        ({"item": ["Л43"], "priority": [1]}, "приоритеты", 0.9),
    ],
)
def test_detect_file_type_confident(columns, expected_type, min_confidence):
    """Well-formed spreadsheets are classified with high confidence."""
    df = make_df(columns)
    result = detect_file_type(df)

    assert isinstance(result, FileTypeResult)
    assert result.file_type == expected_type
    assert result.confidence >= min_confidence
    assert result.reason is not None


@pytest.mark.parametrize(
    "columns",
    [
        {"unknown": [1, 2, 3]},
        {"foo": ["a"], "bar": ["b"]},
    ],
)
def test_detect_file_type_unclear_returns_none(columns):
    """Spreadsheets without recognizable column patterns return None."""
    df = make_df(columns)
    result = detect_file_type(df)

    assert result.file_type is None
    assert result.confidence < 0.9


def test_detect_file_type_low_confidence_when_ambiguous():
    """A mix of clues from different categories lowers confidence."""
    df = make_df({"Номенклатура": ["10"], "Количество": [1000], "Сек/шт": [20]})
    result = detect_file_type(df)

    assert result.file_type is None
    assert result.confidence < 0.9


def test_detect_file_type_empty_dataframe():
    """An empty DataFrame cannot be classified."""
    df = pd.DataFrame()
    result = detect_file_type(df)

    assert result.file_type is None
    assert result.confidence == 0.0
