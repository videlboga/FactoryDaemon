"""High-level planning orchestrator for FactoryDaemon sessions.

This module implements the conversation flow:
- parse uploaded tables;
- detect their type (остатки / нормы / приоритеты);
- extract values into a UserSession;
- determine the next action (ask for missing data, run planner, report underload, produce report).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd

from factorydaemon.planner.engine import plan as plan_shift
from factorydaemon.planner.file_type import FileTypeResult, detect_file_type
from factorydaemon.planner.parser import ParseError, parse_file
from factorydaemon.planner.session import Step, UserSession
from factorydaemon.planner.validator import ValidationError, check_plan, validate_plan_inputs
from factorydaemon.storage.norms import NormStorage

if TYPE_CHECKING:
    pass


class PlanningResult:
    """Outcome of one planning step."""

    def __init__(
        self,
        session: UserSession,
        reply: str,
        excel_path: Path | None = None,
        errors: list[ValidationError] | None = None,
    ):
        self.session = session
        self.reply = reply
        self.excel_path = excel_path
        self.errors = errors or []


_POSITION_KEYS = {
    "номенклатура",
    "деталь",
    "позиция",
    "position",
    "item",
    "part",
    "product",
    "изделие",
    "nomer",
    "nomer_p_p",
    "nomer_pp",
    "pp",
    "номер",
    "id",
    "код",
    "наименование",
}
_QUANTITY_KEYS = {
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
_TIME_KEYS = {
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
}
_PRIORITY_KEYS = {
    "приоритет",
    "важность",
    "priority",
    "rank",
    "порядок",
    "order",
}


def _find_column(df: pd.DataFrame, keys: set[str]) -> str | None:
    """Find a DataFrame column matching one of the key fingerprints."""
    for col in df.columns:
        normalized = str(col).strip().lower()
        normalized = normalized.replace("№", "nomer")
        normalized = normalized.replace("/", "_").replace("\\", "_").replace("-", "_")
        normalized = normalized.replace(" ", "_").replace(".", "_")
        normalized = "".join(ch for ch in normalized if ch.isalnum() or ch == "_")
        if normalized in keys or any(normalized.startswith(k + "_") for k in keys):
            return str(col)
    return None




def _expected_file_type(session: UserSession) -> str:
    """Return the next file type the session is waiting for (sequential mode)."""
    if not session.demands:
        return "остатки"
    if not session.norms:
        return "нормы"
    return "приоритеты"


def _guess_fallback_type(session: UserSession) -> str | None:
    """Return the most likely missing file type based on session state."""
    if not session.demands:
        return "остатки"
    if not session.norms:
        return "нормы"
    if not session.priorities:
        return "приоритеты"
    return None


def _extract_columns(df: pd.DataFrame, file_type: str) -> tuple[str, str | None, bool] | None:
    """Return (position_col, value_col, use_row_order) for known file types.

    In sequential mode we trust the file type and fall back to the first two columns.
    Priorities may be a single ordered position column (row order = priority).
    If the priority value column is not a recognised priority header, we treat the
    file as an ordered list and ignore the numeric values in the second column.
    """
    if len(df.columns) < 1:
        return None
    pos_col = _find_column(df, _POSITION_KEYS)
    if not pos_col:
        pos_col = str(df.columns[0])
    use_row_order = False
    if file_type == "остатки":
        val_col = _find_column(df, _QUANTITY_KEYS)
    elif file_type == "нормы":
        val_col = _find_column(df, _TIME_KEYS)
    elif file_type == "приоритеты":
        val_col = _find_column(df, _PRIORITY_KEYS)
        if val_col:
            pass  # explicit priority numbers
        elif len(df.columns) == 1:
            # Single-column file: ordered positions only.
            return pos_col, None, True
        else:
            # Fallback second column: treat as ordered list.
            candidates = [c for c in df.columns if str(c) != pos_col]
            val_col = str(candidates[0]) if candidates else None
            use_row_order = True
    else:
        return None
    if not val_col:
        # Sequential fallback: use the first non-position column.
        candidates = [c for c in df.columns if str(c) != pos_col]
        val_col = str(candidates[0]) if candidates else None
    return pos_col, val_col, use_row_order


def _reply(reply: str, extra: str) -> str:
    """Join two text blocks with a blank line if both are non-empty."""
    parts = [p for p in (reply.strip(), extra.strip()) if p]
    return "\n\n".join(parts)


def ingest_file(session: UserSession, source: str | Path) -> PlanningResult:
    """Parse a file, classify it, and merge its data into the session."""
    logger = logging.getLogger(__name__)
    try:
        df = parse_file(source)
    except ParseError as exc:
        return PlanningResult(session, f"Не удалось прочитать файл: {exc}")

    expected_type = _expected_file_type(session)
    logger.info(
        "Sequential mode: expecting %s, columns: %s",
        expected_type,
        list(df.columns),
    )
    classification = FileTypeResult(
        file_type=expected_type,
        confidence=1.0,
        reason=f"Sequential upload step expects {expected_type}.",
    )

    if classification.file_type is None:
        return PlanningResult(
            session,
            "Не понял тип файла ("
            + classification.reason
            + "). Пришлите таблицу с остатками, нормами или приоритетами.",
        )

    cols = _extract_columns(df, classification.file_type)
    logger.info("Matched columns for %s: %s", classification.file_type, cols)
    if cols is None:
        return PlanningResult(
            session,
            "Определил файл как " + classification.file_type + ", но не нашёл нужных колонок. "
            "Причина: " + classification.reason + ".",
        )

    pos_col, val_col, use_row_order = cols
    if classification.file_type == "остатки":
        session.update_demands(df, pos_col, val_col)
        reply = f"Принял остатки: {len(session.demands)} позиций."
    elif classification.file_type == "нормы":
        session.update_norms(df, pos_col, val_col)
        reply = f"Принял нормы: {len(session.norms)} позиций."
    else:
        session.update_priorities(df, pos_col, val_col, use_row_order=use_row_order)
        reply = f"Принял приоритеты: {len(session.priorities)} позиций."

    return advance_session(session, initial_reply=reply)


def advance_session(session: UserSession, initial_reply: str = "") -> PlanningResult:
    """Decide what to do next based on the current session state."""
    missing_norms = session.missing_norms_positions
    if missing_norms and not session.asked_for_norms:
        session.asked_for_norms = True
        session.step = Step.MISSING_NORMS
        positions = ", ".join(f"`{p}`" for p in missing_norms[:10])
        more = " и др." if len(missing_norms) > 10 else ""
        extra = f"Нужны нормы (сек/шт) для позиций: {positions}{more}. Пришлите файл с нормами."
        return PlanningResult(session, _reply(initial_reply, extra))

    missing_priorities = session.missing_priorities_positions
    if missing_priorities and not session.asked_for_priorities:
        session.asked_for_priorities = True
        session.step = Step.MISSING_PRIORITIES
        positions = ", ".join(f"`{p}`" for p in missing_priorities[:10])
        more = " и др." if len(missing_priorities) > 10 else ""
        extra = f"Нужны приоритеты для позиций: {positions}{more}. Пришлите файл с приоритетами."
        return PlanningResult(session, _reply(initial_reply, extra))

    # If user already sent a priorities file but some positions are still missing,
    # assign them the lowest default priority so planning can proceed.
    for pos in session.missing_priorities_positions:
        session.priorities[pos] = 0

    # Positions without norms cannot be planned; silently exclude them.
    missing_norms = session.missing_norms_positions
    if missing_norms:
        for pos in list(session.demands.keys()):
            if pos not in session.norms:
                session.demands.pop(pos, None)
                session.priorities.pop(pos, None)
        info = f"Внимание: {len(missing_norms)} позиций без норм исключены из плана.\n"
    else:
        info = ""

    if not session.is_ready_to_plan:
        return PlanningResult(
            session,
            _reply(initial_reply, "Жду остатки, нормы и приоритеты, чтобы составить план."),
        )

    if session.target_workers is None:
        session.step = Step.ASKING_WORKERS
        extra = (
            f"{info}Данные собраны. На сколько работников считать план? "
            "Ответьте числом (например, 2)."
        )
        return PlanningResult(session, _reply(initial_reply, extra))

    session.step = Step.READY_TO_PLAN
    return run_planner(session, initial_reply=initial_reply)


def run_planner(session: UserSession, initial_reply: str = "") -> PlanningResult:
    """Run the planner and handle underload logic."""
    norms = NormStorage(session.norms)
    input_errors = validate_plan_inputs(session.demands, session.priorities, norms)
    if input_errors:
        session.step = Step.COLLECTING
        msgs = "\n".join(f"- {e.message}" for e in input_errors[:5])
        extra = "Ошибки в данных:\n" + msgs
        return PlanningResult(session, _reply(initial_reply, extra))

    plan_result = plan_shift(
        demands=session.demands,
        priorities=session.priorities,
        norms=norms,
        shift_hours=session.shift_hours,
        max_positions_per_worker=session.max_positions_per_worker,
        target_worker_count=session.target_workers,
    )
    session.plan_result = plan_result
    session.warnings = []

    utilization = plan_result.utilization
    if (
        utilization < session.underload_threshold
        and not session.asked_for_more_priorities_underload
    ):
        session.asked_for_more_priorities_underload = True
        session.step = Step.UNDERLOAD
        extra = (
            f"Посчитал план: {plan_result.worker_count} работник(ов), "
            f"средняя загрузка {utilization * 100:.1f}%. Это ниже порога "
            f"{session.underload_threshold * 100:.0f}%. "
            "Пришлите ещё приоритеты/позиции, чтобы загрузить смену полнее."
        )
        return PlanningResult(session, _reply(initial_reply, extra))

    errors = check_plan(
        session.demands, session.priorities, norms, plan_result, session.shift_hours
    )
    if errors:
        session.step = Step.COLLECTING
        msgs = "\n".join(f"- {e.message}" for e in errors[:5])
        extra = "План не прошёл проверку:\n" + msgs
        return PlanningResult(session, _reply(initial_reply, extra))

    session.step = Step.PLAN_READY
    extra = (
        f"План готов: {plan_result.worker_count} работник(ов), "
        f"средняя загрузка {utilization * 100:.1f}%. Сейчас сгенерирую Excel-отчёт."
    )
    return PlanningResult(session, _reply(initial_reply, extra))


def finish_plan(session: UserSession, output_path: Path) -> PlanningResult:
    """Generate Excel report and finalize the session."""
    if session.plan_result is None:
        return PlanningResult(session, "Нет готового плана. Сначала составьте план.")

    from factorydaemon.planner.excel import write_excel_report

    report = write_excel_report(session.plan_result, output_path, warnings=session.warnings)
    session.step = Step.FINISHED
    return PlanningResult(
        session,
        f"Отчёт готов: {report.path.name}. Скачайте файл выше.",
        excel_path=report.path,
    )
