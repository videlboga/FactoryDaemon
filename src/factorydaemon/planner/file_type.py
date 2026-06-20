"""Automatic classification of uploaded spreadsheets.

FactoryDaemon accepts three kinds of tabular input:

* **остатки** — position + quantity (plan/remaining items to process);
* **нормы** — position + time per unit (seconds per piece);
* **приоритеты** — position + priority rank.

This module inspects the column headers of a pandas DataFrame and returns a
``FileTypeResult`` with a confidence score. Callers should ask the user for
clarification when confidence is below ``0.9``.

"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pandas as pd


@dataclass(frozen=True, slots=True)
class FileTypeResult:
    """Classification result for an uploaded file.

    Attributes:
        file_type: One of ``остатки``, ``нормы``, ``приоритеты`` or ``None``
            when the type cannot be determined confidently.
        confidence: Score between ``0.0`` and ``1.0``. Values below ``0.9``
            should trigger a clarifying question in the bot interface.
        reason: Human-readable explanation of how the decision was made.
    """

    file_type: str | None
    confidence: float
    reason: str


# Column-key fingerprints. Keys are lower-cased and stripped of punctuation
# so that user-authored headers such as "Сек/шт", "сек_шт" or "seconds_per_unit"
# all match the same concept.
_POSITION_KEYS = frozenset(
    {
        "номенклатура",
        "деталь",
        "позиция",
        "position",
        "item",
        "part",
        "product",
        "изделие",
        "наименование",
        "nomer",
        "nomer_p_p",
        "nomer_pp",
        "pp",
        "id",
        "код",
        "№",
        "№_пп",
        "№пп",
        "номер",
    }
)
_QUANTITY_KEYS = frozenset(
    {
        "количество",
        "план",
        "остаток",
        "quantity",
        "count",
        "amount",
        "остатки",
        "колво",
        "кол_во",
    }
)
_TIME_KEYS = frozenset(
    {
        "время",
        "секшт",
        "сек_шт",
        "норма",
        "time",
        "seconds",
        "seconds_per_unit",
        "sec_per_unit",
        "sec",
        "трудоемкость",
        "трудоёмкость",
        "rate",
        "время_обработки",
        "время_обработки_сек",
        "сек",
    }
)
_PRIORITY_KEYS = frozenset(
    {
        "приоритет",
        "важность",
        "priority",
        "rank",
        "порядок",
        "order",
    }
)


def _normalize_header(value: object) -> str:
    """Convert any header value to a comparable lower-case latin/cyrillic key."""
    if value is None:
        return ""
    text = str(value).strip().lower()
    # Replace common separators with underscores, then collapse.
    text = text.replace("/", "_").replace("\\", "_").replace("-", "_")
    text = text.replace(" ", "_").replace(".", "_")
    # Treat the № symbol as an id marker.
    text = text.replace("№", "nomer")
    # Remove non-alphanumeric/underscore characters to keep comparability.
    return "".join(ch for ch in text if ch.isalnum() or ch == "_")


def _header_matches(header: str, key_set: frozenset[str]) -> bool:
    """Return True when the normalized header belongs to a concept set."""
    normalized = _normalize_header(header)
    return normalized in key_set or any(normalized.startswith(k + "_") for k in key_set)


def _detect_priority_by_values(df: pd.DataFrame) -> bool:
    """Heuristic: a numeric column with small integers (1-10) looks like priority ranks."""
    for col in df.columns:
        try:
            values = pd.to_numeric(df[col], errors="coerce").dropna()
        except Exception:
            continue
        if len(values) == 0:
            continue
        if values.dtype.kind not in "iufb":
            continue
        unique = values.unique()
        if len(unique) > 20 or len(unique) < 2:
            continue
        if values.min() >= 1 and values.max() <= 10:
            return True
    return False


def detect_file_type(df: pd.DataFrame) -> FileTypeResult:
    """Classify a DataFrame as one of the known FactoryDaemon file types.

    The function scores every known column fingerprint found in the header and
    normalizes the score into a confidence value. A clear single-type signal
    yields confidence ``>= 0.9``; mixed or weak signals stay below the threshold.

    Args:
        df: Input DataFrame. Empty or header-less DataFrames return
            ``file_type=None`` with zero confidence.

    Returns:
        A ``FileTypeResult`` describing the detected type and confidence.

    """
    if df is None or df.empty:
        return FileTypeResult(
            file_type=None,
            confidence=0.0,
            reason="DataFrame is empty or missing; cannot determine file type.",
        )

    headers = [str(col) for col in df.columns]
    if not headers:
        return FileTypeResult(
            file_type=None,
            confidence=0.0,
            reason="No column headers found.",
        )

    # Assign a concrete evidence token to each header for the most specific
    # category it supports. A single position header only counts as evidence
    # when paired with a type-specific value header.
    #
    # Evidence tokens:
    #   - position header + quantity value -> остатки
    #   - position header + time value     -> нормы
    #   - position header + priority value  -> приоритеты
    #   - position-only header              -> needs a value column to count
    evidence: dict[str, int] = {"остатки": 0, "нормы": 0, "приоритеты": 0}
    matched_by_header: dict[str, list[str]] = {}

    for header in headers:
        candidates: list[str] = []
        if _header_matches(header, _POSITION_KEYS):
            candidates.extend(["остатки", "нормы", "приоритеты"])
        if _header_matches(header, _QUANTITY_KEYS):
            candidates.append("остатки")
        if _header_matches(header, _TIME_KEYS):
            candidates.append("нормы")
        if _header_matches(header, _PRIORITY_KEYS):
            candidates.append("приоритеты")

        if candidates:
            matched_by_header[header] = candidates

    # Give category-specific value headers the strongest weight.
    for header in headers:
        if _header_matches(header, _QUANTITY_KEYS):
            evidence["остатки"] += 1
        if _header_matches(header, _TIME_KEYS):
            evidence["нормы"] += 1
        if _header_matches(header, _PRIORITY_KEYS):
            evidence["приоритеты"] += 1

    # Add a position bonus only when there is at most one type-specific value
    # column, and assign the bonus to the category with the strongest value
    # signal to break ties. If value columns from different categories are
    # present alongside a position column, skip the bonus so the value headers
    # speak for themselves and mixed sheets stay ambiguous.
    has_position = any(_header_matches(header, _POSITION_KEYS) for header in headers)
    value_categories = sum(1 for v in evidence.values() if v > 0)
    if has_position and value_categories <= 1:
        leader = max(evidence, key=lambda k: evidence[k])
        if evidence[leader] > 0:
            evidence[leader] += 1

    total_evidence = sum(evidence.values())
    if total_evidence == 0:
        return FileTypeResult(
            file_type=None,
            confidence=0.0,
            reason=f"No recognizable column headers found in {headers!r}.",
        )

    ranked = sorted(evidence.items(), key=lambda item: (-item[1], item[0]))
    best_type, best_score = ranked[0]
    second_score = ranked[1][1] if len(ranked) > 1 else 0

    # Require at least two evidence tokens and a strict leader, OR a single
    # strong value signal when there is a position-ish column to pair with.
    has_position_like = any(_header_matches(h, _POSITION_KEYS) for h in headers)
    single_value_ok = has_position_like and best_score == 1 and second_score == 0
    if not single_value_ok and (best_score < 2 or second_score >= best_score):
        # If we have at least a position column and a numeric column with small
        # integer values, treat it as priorities regardless of header noise.
        if has_position_like and _detect_priority_by_values(df):
            return FileTypeResult(
                file_type="приоритеты",
                confidence=0.7,
                reason=(
                    f"Position column + small integer values look like priority ranks. {evidence}."
                ),
            )
        confidence = best_score / (total_evidence + 1)
        reason = f"Ambiguous header signals: {evidence}. Matched columns: {matched_by_header}."
        return FileTypeResult(file_type=None, confidence=round(confidence, 3), reason=reason)

    # Two clean pieces of evidence with no competing signal -> high confidence.
    confidence = 1.0 if (best_score == 2 and second_score == 0) else best_score / total_evidence

    primary_header = next(
        (h for h in headers if best_type in matched_by_header.get(h, [])),
        headers[0],
    )
    secondary_header = next(
        (h for h in headers if h != primary_header and best_type in matched_by_header.get(h, [])),
        None,
    )
    columns_text = f"{primary_header}"
    if secondary_header:
        columns_text += f", {secondary_header}"

    reason = f"Detected '{best_type}' from columns: {columns_text}."

    # Post-process: if classified as demand but the value column contains
    # only small integers (1-10), it is more likely a priority file.
    if best_type == "остатки" and has_position_like:
        for col in df.columns:
            if not _header_matches(col, _QUANTITY_KEYS):
                continue
            try:
                values = pd.to_numeric(df[col], errors="coerce").dropna()
            except Exception:
                continue
            if (
                len(values) > 0
                and values.min() >= 1
                and values.max() <= 10
                and (values % 1 == 0).all()
            ):
                best_type = "приоритеты"
                confidence = max(0.7, confidence - 0.1)
                reason = f"Small integer values look like priorities; {reason}"
                break

    return FileTypeResult(
        file_type=best_type,
        confidence=round(confidence, 3),
        reason=reason,
    )
