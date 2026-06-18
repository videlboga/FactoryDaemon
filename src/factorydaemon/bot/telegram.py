"""Telegram bot for FactoryDaemon.

Handles:
- document uploads (Excel, CSV, ODS, text);
- copy-pasted tables;
- the planning conversation flow (collect -> clarify -> plan -> report);
- LLM-powered natural language explanations.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from aiogram import Bot, Dispatcher, F, types
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

from factorydaemon.config import settings
from factorydaemon.planner.orchestrator import PlanningResult, finish_plan, ingest_file
from factorydaemon.planner.session import Step, UserSession
from factorydaemon.services.llm import LLMError, explain_plan_issue


class PlanStates(StatesGroup):
    collecting = State()
    planning = State()
    reporting = State()


def _session_path(chat_id: int) -> Path:
    """Path to on-disk session JSON."""
    tmpdir = Path(tempfile.gettempdir()) / "factorydaemon-sessions"
    tmpdir.mkdir(parents=True, exist_ok=True)
    return tmpdir / f"session_{chat_id}.json"


def _load_session(chat_id: int) -> UserSession:
    """Load or create a user session."""
    path = _session_path(chat_id)
    if path.exists():
        try:
            import json

            with open(path, encoding="utf-8") as f:
                return UserSession.from_dict(json.load(f))
        except Exception:
            pass
    return UserSession(session_id=f"tg-{chat_id}")


def _save_session(session: UserSession, chat_id: int) -> None:
    """Persist session to disk."""
    import json

    path = _session_path(chat_id)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(session.to_dict(), f, ensure_ascii=False, indent=2)


async def _reply_or_explain(result: PlanningResult, message: types.Message) -> None:
    """Send text reply; use LLM to rephrase if there are validation errors."""
    if result.errors:
        try:
            msgs = [{"role": "user", "content": result.reply}]
            explanation = await explain_plan_issue(msgs)
            await message.answer(explanation, parse_mode=ParseMode.MARKDOWN)
            return
        except LLMError:
            pass
    await message.answer(result.reply, parse_mode=ParseMode.MARKDOWN)


async def _handle_file_upload(
    message: types.Message,
    state: FSMContext,
    file_bytes: bytes,
    text_source: bool = False,
) -> None:
    """Process any incoming file or pasted table."""
    chat_id = message.chat.id
    session = _load_session(chat_id)

    file_name = (
        message.document.file_name
        if message.document is not None and message.document.file_name is not None
        else "upload.xlsx"
    )
    suffix = ".txt" if text_source else Path(file_name).suffix
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(file_bytes)
        tmp_path = Path(tmp.name)

    try:
        result = ingest_file(session, tmp_path)
    finally:
        os.unlink(tmp_path)

    _save_session(result.session, chat_id)

    if result.excel_path:
        document = types.FSInputFile(result.excel_path)
        await message.answer_document(document, caption=result.reply)
    else:
        await _reply_or_explain(result, message)

    if result.session.step == Step.FINISHED:
        await state.set_state(PlanStates.collecting)
    elif result.session.step == Step.PLAN_READY:
        await state.set_state(PlanStates.reporting)
    elif result.session.step == Step.READY_TO_PLAN:
        output = Path(tempfile.gettempdir()) / f"plan_{chat_id}.xlsx"
        result2 = finish_plan(result.session, output)
        _save_session(result2.session, chat_id)
        if result2.excel_path:
            document = types.FSInputFile(result2.excel_path)
            await message.answer_document(document, caption=result2.reply)
        else:
            await _reply_or_explain(result2, message)
        await state.set_state(PlanStates.collecting)
    else:
        await state.set_state(PlanStates.collecting)


def get_bot() -> Bot:
    """Lazy Telegram Bot instance."""
    token = settings.telegram_bot_token
    if not token:
        raise RuntimeError("FD_TELEGRAM_BOT_TOKEN is not set")
    return Bot(token=token)


dp = Dispatcher(storage=MemoryStorage())


@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext) -> None:
    await state.set_state(PlanStates.collecting)
    text = """Привет! Я планировщик производственных смен FactoryDaemon.

Пришлите мне файлы или таблицы:
1. Остатки/объёмы (колонки: позиция, количество)
2. Нормы времени (колонки: позиция, сек/шт)
3. Приоритеты (колонки: позиция, приоритет)

Когда данных хватит, я составлю план и пришлю Excel."""
    await message.answer(text, parse_mode=ParseMode.MARKDOWN)


@dp.message(Command("reset"))
async def cmd_reset(message: types.Message, state: FSMContext) -> None:
    chat_id = message.chat.id
    path = _session_path(chat_id)
    if path.exists():
        os.unlink(path)
    await state.set_state(PlanStates.collecting)
    await message.answer("Сессия сброшена. Пришлите данные заново.")


@dp.message(Command("plan"))
async def cmd_plan(message: types.Message, state: FSMContext) -> None:
    chat_id = message.chat.id
    session = _load_session(chat_id)
    from factorydaemon.planner.orchestrator import run_planner

    result = run_planner(session)
    _save_session(result.session, chat_id)

    if result.session.step.name == "PLAN_READY":
        output = Path(tempfile.gettempdir()) / f"plan_{chat_id}.xlsx"
        result2 = finish_plan(result.session, output)
        _save_session(result2.session, chat_id)
        if result2.excel_path:
            document = types.FSInputFile(result2.excel_path)
            await message.answer_document(document, caption=result2.reply)
        else:
            await _reply_or_explain(result2, message)
    else:
        await _reply_or_explain(result, message)


@dp.message(F.document)
async def handle_document(message: types.Message, state: FSMContext) -> None:
    document = message.document
    if document is None or document.file_id is None:
        return
    bot = get_bot()
    file_obj = await bot.get_file(document.file_id)
    if file_obj.file_path is None:
        return
    file_stream = await bot.download_file(file_obj.file_path)
    if file_stream is None:
        return
    await _handle_file_upload(message, state, file_stream.read())


@dp.message(F.text)
async def handle_text(message: types.Message, state: FSMContext) -> None:
    text = message.text or ""
    if text.startswith("/"):
        return
    if "\n" in text or "\t" in text or "|" in text:
        await _handle_file_upload(message, state, text.encode("utf-8"), text_source=True)
        return

    session = _load_session(message.chat.id)
    try:
        prompt = f"Пользователь написал: {text}. Текущее состояние сессии: {session.to_dict()}"
        reply = await explain_plan_issue([{"role": "user", "content": prompt}])
    except LLMError as exc:
        reply = f"Не получилось обработать сообщение: {exc}. Пришлите таблицу или файл."
    await message.answer(reply)


def main() -> None:
    """Entrypoint for the Telegram bot."""
    import asyncio

    bot = get_bot()
    asyncio.run(dp.start_polling(bot))


if __name__ == "__main__":
    main()
