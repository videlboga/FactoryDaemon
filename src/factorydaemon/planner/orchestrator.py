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




def _guess_fallback_type(session: UserSession) -> str | None:
    """Return the most likely missing file type based on session state."""
    if not session.demands:
        return "остатки"
    if not session.norms:
        return "нормы"
    if not session.priorities:
        return "приоритеты"
    return None


def _extract_columns(df: pd.DataFrame, file_type: str) -> tuple[str, str] | None:
    """Return (position_col, value_col) for known file types."""
    pos_col = _find_column(df, _POSITION_KEYS)
    if not pos_col:
        # Fallback: use the first column as a position key if it looks like an id/number.
        first_col = str(df.columns[0])
        normalized = ''.join(ch for ch in first_col.lower() if ch.isalnum() or ch == ' ')
        if any(k in normalized for k in {'№', 'номер', 'пп', 'id', 'код'}):
            pos_col = first_col
        else:
            return None
    if file_type == "остатки":
        val_col = _find_column(df, _QUANTITY_KEYS)
    elif file_type == "нормы":
        val_col = _find_column(df, _TIME_KEYS)
    elif file_type == "приоритеты":
        val_col = _find_column(df, _PRIORITY_KEYS)
    else:
        return None
    if not val_col:
        return None
    return pos_col, val_col


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

    classification = detect_file_type(df)
    logger.info(
        "File classified as %s (reason: %s), columns: %s",
        classification.file_type,
        classification.reason,
        list(df.columns),
    )

    # If classifier says demand but we already have demands and values are small integers, treat as priorities.
    if classification.file_type == "остатки" and session.demands:
        qty_col = _find_column(df, _QUANTITY_KEYS)
        if qty_col:
            try:
                values = pd.to_numeric(df[qty_col], errors="coerce").dropna()
                if (
                    len(values) > 0
                    and values.min() >= 1
                    and values.max() <= 10
                    and (values % 1 == 0).all()
                ):
                    classification = FileTypeResult(
                        file_type="приоритеты",
                        confidence=0.7,
                        reason=f"Small integer values look like priorities; {classification.reason}",
                    )
                    logger.info("Reclassified demand file as priorities due to small integer values")
                    df = df.rename(columns={qty_col: "Приоритет"})
            except Exception:
                pass
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

    pos_col, val_col = cols
    if classification.file_type == "остатки":
        session.update_demands(df, pos_col, val_col)
        reply = f"Принял остатки: {len(session.demands)} позиций."
    elif classification.file_type == "нормы":
        session.update_norms(df, pos_col, val_col)
        reply = f"Принял нормы: {len(session.norms)} позиций."
    else:
        session.update_priorities(df, pos_col, val_col)
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

    if not session.is_ready_to_plan:
        return PlanningResult(
            session,
            _reply(initial_reply, "Жду остатки, нормы и приоритеты, чтобы составить план."),
        )

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
