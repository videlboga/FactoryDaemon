"""Spreadsheet and copy-paste parser for FactoryDaemon.

This module loads tabular data from Excel (xlsx/xls), CSV, ODS and from plain
text tables pasted into Telegram (Markdown tables, TSV, or whitespace-aligned
columns). It always returns a ``pandas.DataFrame``.
"""

from __future__ import annotations

import csv
import io
import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pandas as pd


class ParseError(ValueError):
    """Raised when an input cannot be parsed as a table."""


# Text patterns used to recognise table-like input.
_MD_LINE = re.compile(r"^\s*\|?(.+?)\|?\s*$", re.UNICODE)
_WS_LINE = re.compile(r"\S+", re.UNICODE)


def _read_bytes(path: Path) -> bytes:
    if not path.exists():
        raise ParseError(f"File not found: {path}")
    return path.read_bytes()


def _read_text_with_bom(data: bytes, encoding: str | None = None) -> str:
    """Decode bytes to text, stripping UTF-8/16 BOM if present."""
    if data.startswith(b"\xef\xbb\xbf"):
        return data.decode("utf-8-sig")
    if data.startswith(b"\xff\xfe") or data.startswith(b"\xfe\xff"):
        return data.decode("utf-16")
    enc = encoding or "utf-8"
    return data.decode(enc)


def _normalize_header(value: object) -> str:
    """Trim whitespace and collapse repeated spaces in a column name."""
    text = str(value).strip()
    return re.sub(r"\s+", " ", text)


def _normalize_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """Clean column names and drop fully-empty rows/columns."""
    if df.empty:
        raise ParseError("Parsed table is empty")

    df = df.copy()
    df.columns = [_normalize_header(col) for col in df.columns]
    # Drop rows/columns that are entirely NaN.
    df = df.dropna(axis=0, how="all").dropna(axis=1, how="all")
    if df.empty:
        raise ParseError("Parsed table contains no data")
    # Reset index for predictable shape.
    return df.reset_index(drop=True)


def _parse_xlsx(path: Path) -> pd.DataFrame:
    import pandas as pd

    if not path.exists():
        raise ParseError(f"File not found: {path}")
    try:
        return pd.read_excel(path, engine="openpyxl")
    except Exception as exc:  # noqa: BLE001
        raise ParseError(f"Failed to read xlsx file {path}: {exc}") from exc


def _parse_xls(path: Path) -> pd.DataFrame:
    import pandas as pd

    if not path.exists():
        raise ParseError(f"File not found: {path}")
    try:
        return pd.read_excel(path, engine="xlrd")
    except Exception as exc:  # noqa: BLE001
        raise ParseError(f"Failed to read xls file {path}: {exc}") from exc


def _parse_ods(path: Path) -> pd.DataFrame:
    import pandas as pd

    if not path.exists():
        raise ParseError(f"File not found: {path}")
    try:
        return pd.read_excel(path, engine="odf")
    except Exception as exc:  # noqa: BLE001
        raise ParseError(f"Failed to read ods file {path}: {exc}") from exc


def _sniff_csv_delimiter(text: str) -> str:
    """Use the csv module to detect the most likely delimiter."""
    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,\t|")
        return dialect.delimiter
    except csv.Error:
        return ";"


def _parse_csv(path: Path) -> pd.DataFrame:
    import pandas as pd

    if not path.exists():
        raise ParseError(f"File not found: {path}")
    text = _read_text_with_bom(path.read_bytes())
    if not text.strip():
        raise ParseError("CSV file is empty")
    delimiter = _sniff_csv_delimiter(text)
    df = _try_read_csv(text, delimiter)
    if df is None:
        # The sniffer may have been confused by commas inside the header.
        for delim in [",", ";", "	", "|", ":", " "]:
            if delim == delimiter:
                continue
            df = _try_read_csv(text, delim)
            if df is not None and len(df.columns) > 1:
                break
    if df is None:
        raise ParseError(f"Failed to read csv file {path}")
    return df


def _try_read_csv(text: str, delimiter: str):
    """Attempt to parse CSV; return None if result looks malformed."""
    import pandas as pd

    try:
        df = pd.read_csv(io.StringIO(text), delimiter=delimiter, dtype=str, keep_default_na=False)
    except Exception:
        return None
    if df.empty or len(df.columns) < 1:
        return None
    return df


def _looks_like_markdown_table(lines: list[str]) -> bool:
    return any("|" in line for line in lines[:10])


def _parse_markdown_table(text: str) -> pd.DataFrame:
    import pandas as pd

    raw_lines = [line for line in text.splitlines() if _MD_LINE.match(line)]
    if len(raw_lines) < 2:
        raise ParseError("Markdown table requires at least a header and one data row")

    # Drop separator lines such as | --- | --- |.
    data_lines: list[list[str]] = []
    for line in raw_lines:
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        cells = [c for c in cells if c or c == ""]
        if all(re.match(r"^[:-]+$", c) for c in cells if c):
            continue
        data_lines.append(cells)

    if len(data_lines) < 2:
        raise ParseError("Markdown table contains no data rows")

    header = data_lines[0]
    rows = data_lines[1:]
    max_cols = max(len(header), max((len(r) for r in rows), default=len(header)))
    header = header + [""] * (max_cols - len(header))
    rows = [r + [""] * (max_cols - len(r)) for r in rows]

    return pd.DataFrame(rows, columns=header)


def _parse_plain_text_table(text: str) -> pd.DataFrame:
    import pandas as pd

    lines = [line.rstrip() for line in text.splitlines() if _WS_LINE.search(line)]
    if len(lines) < 2:
        raise ParseError("Text input does not look like a table")

    # Try whitespace splitting first; if row lengths vary, fall back to pandas read_fwf.
    split_rows = [re.split(r"\s+", line.strip()) for line in lines]
    if len({len(r) for r in split_rows}) == 1 and len(split_rows[0]) >= 2:
        return pd.DataFrame(split_rows[1:], columns=split_rows[0])

    try:
        return pd.read_fwf(io.StringIO(text))
    except Exception as exc:  # noqa: BLE001
        raise ParseError(f"Failed to parse text table: {exc}") from exc


def _parse_text_table(text: str) -> pd.DataFrame:
    if not text or not text.strip():
        raise ParseError("Input is empty")

    lines = text.strip().splitlines()
    if _looks_like_markdown_table(lines):
        return _parse_markdown_table(text)

    return _parse_plain_text_table(text)


def parse_file(source: str | Path) -> pd.DataFrame:
    """Parse a spreadsheet file or a text table into a DataFrame.

    Supported file formats:
        * ``.xlsx`` — Microsoft Excel 2007+
        * ``.xls`` — Legacy Microsoft Excel
        * ``.csv`` — comma / semicolon / tab separated values
        * ``.ods`` — OpenDocument Spreadsheet

    Supported text inputs:
        * Markdown tables (``| col1 | col2 |``)
        * Tab-separated or whitespace-separated copy-paste
        * Fixed-width aligned text

    Args:
        source: Either a filesystem path or a string containing a table.

    Returns:
        A ``pandas.DataFrame`` with cleaned column headers.

    Raises:
        ParseError: If the input is empty, unreadable, or not table-shaped.

    """
    if isinstance(source, str):
        stripped = source.strip()
        if not stripped:
            raise ParseError("Input is empty")
        # If the string contains newlines or obvious table markers, treat as text.
        if "\n" in source or "\t" in source or "|" in source:
            return _normalize_dataframe(_parse_text_table(source))
        # Single-line string that does not look like a path: not a table.
        if not Path(source).exists():
            raise ParseError("Input does not look like a table or an existing file path")

    path = Path(source)
    suffix = path.suffix.lower()

    if suffix == ".xlsx":
        df = _parse_xlsx(path)
    elif suffix == ".xls":
        df = _parse_xls(path)
    elif suffix == ".ods":
        df = _parse_ods(path)
    elif suffix == ".csv":
        df = _parse_csv(path)
    else:
        raise ParseError(f"Unsupported file extension: {suffix or 'none'}")

    return _normalize_dataframe(df)
