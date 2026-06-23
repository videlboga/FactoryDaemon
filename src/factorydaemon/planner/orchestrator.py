"""High-level planning orchestrator for FactoryDaemon sessions."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd

from factorydaemon.planner.engine import plan as plan_shift
from factorydaemon.planner.parser import ParseError, parse_file
from factorydaemon.planner.session import Step, UserSession
from factorydaemon.planner.validator import ValidationError, check_plan, validate_plan_inputs
from factorydaemon.storage.norms import NormStorage

if TYPE_CHECKING:
    pass


class PlanningResult:
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
    if not session.demands:
        return "остатки"
    if not session.norms:
        return "нормы"
    return "приоритеты"


def _extract_columns(df: pd.DataFrame, file_type: str) -> tuple[str, str | None] | None:
    if len(df.columns) < 1:
        return None
    pos_col = _find_column(df, _POSITION_KEYS)
    if not pos_col:
        pos_col = str(df.columns[0])
    if file_type == "остатки":
        val_col = _find_column(df, _QUANTITY_KEYS)
    elif file_type == "нормы":
        val_col = _find_column(df, _TIME_KEYS)
    elif file_type == "приоритеты":
        val_col = _find_column(df, _PRIORITY_KEYS)
        if not val_col and len(df.columns) == 1:
            return pos_col, None
    else:
        return None
    if not val_col:
        candidates = [c for c in df.columns if str(c) != pos_col]
        val_col = str(candidates[0]) if candidates else None
    return pos_col, val_col


def _reply(reply: str, extra: str) -> str:
    parts = [p for p in (reply.strip(), extra.strip()) if p]
    return "\n\n".join(parts)


def ingest_file(session: UserSession, source: str | Path) -> PlanningResult:
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

    cols = _extract_columns(df, expected_type)
    logger.info("Matched columns for %s: %s", expected_type, cols)
    if cols is None:
        return PlanningResult(
            session,
            "Определил файл как " + expected_type + ", но не нашёл нужных колонок. "
            "Пришлите таблицу с остатками, нормами или приоритетами.",
        )

    pos_col, val_col = cols
    if expected_type == "остатки":
        session.update_demands(df, pos_col, val_col)
        reply = f"Принял остатки: {len(session.demands)} позиций."
    elif expected_type == "нормы":
        session.update_norms(df, pos_col, val_col)
        reply = f"Принял нормы: {len(session.norms)} позиций."
    else:
        # The third file is the production plan: it provides both priorities (row
        # order) and planned quantities (demands).
        session.update_priorities(df, pos_col, val_col, is_plan_file=True)
        reply = f"Принял план: {len(session.priorities)} позиций, {len(session.demands)} с объёмами."

    return advance_session(session, initial_reply=reply)


def _collect_warnings(session: UserSession) -> list[str]:
    warnings: list[str] = []
    if session.missing_norms_positions:
        sample = ", ".join(f"`{p}`" for p in session.missing_norms_positions[:10])
        more = " и др." if len(session.missing_norms_positions) > 10 else ""
        warnings.append(
            f"Нет норм (сек/шт) для позиций: {sample}{more}."
        )
    if session.missing_priorities_positions:
        sample = ", ".join(f"`{p}`" for p in session.missing_priorities_positions[:10])
        more = " и др." if len(session.missing_priorities_positions) > 10 else ""
        warnings.append(
            f"Нет приоритетов для позиций: {sample}{more}."
        )
    # Validate that plan quantities do not exceed available stock.
    # `demands` holds the latest uploaded stock; uploaded plan quantities are
    # merged into the same dictionary by update_demands (they share the same
    # quantity column name).  We keep a separate record of plan quantities by
    # overriding with any uploaded priorities file, but here we approximate:
    # the current data model merges them.  We add this validation only when we
    # have already received priorities (so the user knows which positions are in
    # the plan) and at least some stock is present.
    if session.priorities_df is not None and session.demands_df is not None:
        stock: dict[str, float] = {}
        plan_qty: dict[str, float] = {}
        # Stock was uploaded first.
        for _, row in session.demands_df.iterrows():
            pos = str(row.iloc[0]).strip()
            if pos:
                stock[pos] = session.demands.get(pos, 0.0)
        # Plan quantities come from the priorities file (second numeric column).
        # Re-parse them directly to avoid confusion with merged demands.
        df = session.priorities_df
        pos_col = str(df.columns[0])
        numeric_cols = [c for c in df.columns if c != pos_col]
        qty_col = numeric_cols[0] if numeric_cols else None
        if qty_col is not None:
            for _, row in df.iterrows():
                raw = str(row[pos_col]).strip()
                if not raw or raw.lower() == "nan":
                    continue
                from factorydaemon.planner.normalizer import normalize_position
                pos = normalize_position(raw)
                val = row[qty_col]
                try:
                    qty = float(str(val).replace(",", "."))
                    if qty > 0:
                        plan_qty[pos] = qty
                except Exception:
                    pass
        for position, qty in plan_qty.items():
            available = stock.get(position, 0.0)
            if qty > available + 1e-9:
                warnings.append(
                    f"Позиция `{position}`: план {qty:.0f} ед. превышает остаток {available:.0f} ед."
                )
    return warnings


def advance_session(session: UserSession, initial_reply: str = "") -> PlanningResult:
    # Build warnings from whatever data we have so far.
    session.warnings = _collect_warnings(session)

    if not session.demands:
        return PlanningResult(
            session,
            _reply(initial_reply, "Жду остатки, чтобы составить план."),
        )

    if not session.norms:
        return PlanningResult(
            session,
            _reply(initial_reply, "Жду нормы (сек/шт) для расчёта плана."),
        )

    if not session.priorities:
        return PlanningResult(
            session,
            _reply(initial_reply, "Жду приоритеты, чтобы составить план."),
        )

    if session.target_workers is None:
        session.step = Step.ASKING_WORKERS
        return PlanningResult(
            session,
            _reply(initial_reply, "Данные собраны. На сколько работников планировать?"),
        )

    return run_planner(session, initial_reply=initial_reply)


def run_planner(session: UserSession, initial_reply: str = "") -> PlanningResult:
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
        target_workers=session.target_workers,
    )
    session.plan_result = plan_result
    session.warnings.extend(plan_result.warnings)

    actual = plan_result.worker_count
    required = plan_result.required_workers or actual
    utilization = plan_result.utilization

    notes: list[str] = []
    if session.target_workers is not None and actual > session.target_workers:
        notes.append(
            f"⚠️ Указано {session.target_workers} работников, но план требует {actual}. "
            f"Автоматически увеличено до {actual}."
        )
    elif session.target_workers is not None and actual < session.target_workers:
        free = session.target_workers - actual
        notes.append(
            f"План размещён на {actual} работниках из {session.target_workers}. "
            f"Свободная ёмкость для {free} работников — пришлите дополнительные приоритеты/остатки, "
            f"и я дозагружу их."
        )
    else:
        notes.append(
            f"План составлен: {actual} работник(ов), "
            f"средняя загрузка {utilization * 100:.1f}%."
        )

    if session.warnings:
        notes.extend(f"⚠️ {w}" for w in session.warnings)

    session.step = Step.PLAN_READY
    notes.append("Сейчас сгенерирую Excel-отчёт.")
    return PlanningResult(session, _reply(initial_reply, "\n".join(notes)))


def finish_plan(session: UserSession, output_path: Path) -> PlanningResult:
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
