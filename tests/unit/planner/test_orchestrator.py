"""Tests for planner/orchestrator.py."""

from __future__ import annotations

from factorydaemon.planner.orchestrator import ingest_file
from factorydaemon.planner.session import Step, UserSession


def test_orchestrator_asks_for_norms_after_demand(tmp_path):
    csv_path = tmp_path / "demand.csv"
    csv_path.write_text(
        """Номенклатура,Количество
Л43,100
10,200
""",
        encoding="utf-8",
    )
    sess = UserSession(session_id="test")
    result = ingest_file(sess, csv_path)
    assert "Нужны нормы" in result.reply
    assert result.session.step == Step.MISSING_NORMS


def test_orchestrator_asks_for_priorities_after_norms(tmp_path):
    sess = UserSession(session_id="test")
    sess.demands = {"Л43": 100.0, "10": 200.0}
    sess.norms = {"Л43": 20.0, "10": 10.0}
    sess.asked_for_norms = True
    csv_path = tmp_path / "priorities.csv"
    csv_path.write_text(
        """Позиция,Приоритет
Л43,1
""",
        encoding="utf-8",
    )
    result = ingest_file(sess, csv_path)
    assert "Нужны приоритеты" in result.reply or "Жду остатки" in result.reply


def test_orchestrator_ready_to_plan(tmp_path):
    sess = UserSession(session_id="test")
    sess.demands = {"Л43": 100.0, "10": 200.0}
    sess.norms = {"Л43": 20.0, "10": 10.0}
    sess.priorities = {"Л43": 1, "10": 2}
    sess.asked_for_norms = True
    sess.asked_for_priorities = True
    csv_path = tmp_path / "extra.csv"
    csv_path.write_text(
        """Позиция,Приоритет
Л43,1
10,2
""",
        encoding="utf-8",
    )
    result = ingest_file(sess, csv_path)
    assert result.session.step in (
        Step.ASKING_WORKERS,
        Step.READY_TO_PLAN,
        Step.UNDERLOAD,
        Step.PLAN_READY,
    )
