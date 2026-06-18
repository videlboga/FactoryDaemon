"""Tests for the spreadsheet / copy-paste parser."""

from __future__ import annotations

import csv
from pathlib import Path

import pandas as pd
import pytest

from factorydaemon.planner.parser import ParseError, parse_file


def _write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def _make_xlsx(path: Path) -> None:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Data"
    ws.append(["Номенклатура", "Количество"])
    ws.append(["10", 1850])
    ws.append(["Л43", 1200])
    wb.save(path)


def _make_xls(path: Path) -> None:
    import xlwt

    wb = xlwt.Workbook()
    ws = wb.add_sheet("Data")
    ws.write(0, 0, "Деталь")
    ws.write(0, 1, "Сек/шт")
    ws.write(1, 0, "10")
    ws.write(1, 1, 20)
    ws.write(2, 0, "Л43")
    ws.write(2, 1, 10)
    wb.save(path)


def _make_ods(path: Path) -> None:
    from odf import opendocument
    from odf.table import Table, TableCell, TableRow
    from odf.text import P

    doc = opendocument.OpenDocumentSpreadsheet()
    table = Table(name="Data")
    for row_data in [["Позиция", "Приоритет"], ["Л43", "1"], ["Л1", "2"]]:
        tr = TableRow()
        for value in row_data:
            tc = TableCell()
            tc.setAttrNS(
                "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
                "value-type",
                "string",
            )
            tc.addElement(P(text=str(value)))
            tr.addElement(tc)
        table.addElement(tr)
    doc.spreadsheet.addElement(table)
    doc.save(str(path))


def _make_csv(path: Path, delimiter: str = ";") -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, delimiter=delimiter)
        writer.writerow(["Номенклатура", "Количество"])
        writer.writerow(["10", "1850"])
        writer.writerow(["Л43", "1200"])


@pytest.fixture
def sample_dir(tmp_path: Path) -> Path:
    return tmp_path


class TestParseFileSpreadsheets:
    """Parser reads common spreadsheet formats into DataFrames."""

    def test_parse_xlsx(self, sample_dir: Path) -> None:
        path = sample_dir / "plan.xlsx"
        _make_xlsx(path)
        df = parse_file(path)

        assert isinstance(df, pd.DataFrame)
        assert list(df.columns) == ["Номенклатура", "Количество"]
        assert df["Номенклатура"].tolist() == ["10", "Л43"]
        assert df["Количество"].tolist() == [1850, 1200]

    def test_parse_xls(self, sample_dir: Path) -> None:
        path = sample_dir / "norms.xls"
        _make_xls(path)
        df = parse_file(path)

        assert isinstance(df, pd.DataFrame)
        assert list(df.columns) == ["Деталь", "Сек/шт"]
        assert df["Деталь"].tolist() == ["10", "Л43"]
        assert df["Сек/шт"].tolist() == [20, 10]

    def test_parse_csv_semicolon(self, sample_dir: Path) -> None:
        path = sample_dir / "items.csv"
        _make_csv(path, delimiter=";")
        df = parse_file(path)

        assert isinstance(df, pd.DataFrame)
        assert list(df.columns) == ["Номенклатура", "Количество"]
        assert df["Номенклатура"].tolist() == ["10", "Л43"]
        # CSV numbers are read as strings by pandas by default; parser keeps them as-is.
        assert df["Количество"].tolist() == ["1850", "1200"]

    def test_parse_csv_comma(self, sample_dir: Path) -> None:
        path = sample_dir / "items.csv"
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f, delimiter=",")
            writer.writerow(["item", "quantity"])
            writer.writerow(["10", "1850"])
            writer.writerow(["Л43", "1200"])
        df = parse_file(path)

        assert list(df.columns) == ["item", "quantity"]
        assert df["item"].tolist() == ["10", "Л43"]

    def test_parse_ods(self, sample_dir: Path) -> None:
        path = sample_dir / "priorities.ods"
        _make_ods(path)
        df = parse_file(path)

        assert isinstance(df, pd.DataFrame)
        assert list(df.columns) == ["Позиция", "Приоритет"]
        assert df["Позиция"].tolist() == ["Л43", "Л1"]
        assert df["Приоритет"].tolist() == [1, 2]


class TestParseFileTextTables:
    """Parser reads Markdown / tab-separated copy-paste from Telegram."""

    def test_parse_markdown_table(self) -> None:
        text = """| Номенклатура | Количество |
| ------------ | ---------- |
| 10           | 1850       |
| Л43          | 1200       |
"""
        df = parse_file(text)

        assert isinstance(df, pd.DataFrame)
        assert list(df.columns) == ["Номенклатура", "Количество"]
        assert df["Номенклатура"].tolist() == ["10", "Л43"]
        assert df["Количество"].tolist() == ["1850", "1200"]

    def test_parse_tsv_copy_paste(self) -> None:
        text = "Номенклатура\tКоличество\n10\t1850\nЛ43\t1200"
        df = parse_file(text)

        assert list(df.columns) == ["Номенклатура", "Количество"]
        assert df["Номенклатура"].tolist() == ["10", "Л43"]
        assert df["Количество"].tolist() == ["1850", "1200"]

    def test_parse_whitespace_aligned_table(self) -> None:
        text = """Номенклатура   Количество
10             1850
Л43            1200
"""
        df = parse_file(text)

        assert list(df.columns) == ["Номенклатура", "Количество"]
        assert df["Номенклатура"].tolist() == ["10", "Л43"]
        assert df["Количество"].tolist() == ["1850", "1200"]


class TestParseFileErrors:
    """Parser reports unsupported inputs clearly."""

    def test_unsupported_extension_raises(self, sample_dir: Path) -> None:
        path = sample_dir / "data.pdf"
        path.write_text("not a table", encoding="utf-8")
        with pytest.raises(ParseError, match="Unsupported"):
            parse_file(path)

    def test_missing_file_raises(self, sample_dir: Path) -> None:
        with pytest.raises(ParseError, match="not found"):
            parse_file(sample_dir / "missing.xlsx")

    def test_empty_input_raises(self) -> None:
        with pytest.raises(ParseError, match="empty"):
            parse_file("")

    def test_plain_unstructured_text_raises(self) -> None:
        with pytest.raises(ParseError, match="table"):
            parse_file("hello world")


class TestParseFileNormalization:
    """Parser normalizes headers and preserves data types where possible."""

    def test_header_whitespace_trimmed(self, sample_dir: Path) -> None:
        path = sample_dir / "items.csv"
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f, delimiter=";")
            writer.writerow(["  Номенклатура  ", " Количество "])
            writer.writerow(["10", "1850"])
        df = parse_file(path)

        assert list(df.columns) == ["Номенклатура", "Количество"]
