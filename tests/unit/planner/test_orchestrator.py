"""Tests for planner/orchestrator.py."""

from __future__ import annotations

from factorydaemon.planner.orchestrator import ingest_file
from factorydaemon.planner.session import Step, UserSession


def test_orchestrator_tracks_missing_norms(tmp_path):
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
    assert result.session.step == Step.COLLECTING
    assert "нет норм" in result.reply.lower() or "жду нормы" in result.reply.lower()


def test_orchestrator_tracks_missing_priorities(tmp_path):
    sess = UserSession(session_id="test")
    sess.demands = {"Л43": 100.0, "10": 200.0}
    sess.norms = {"Л43": 20.0, "10": 10.0}
    csv_path = tmp_path / "priorities.csv"
    csv_path.write_text(
        """Позиция,Приоритет
Л43,1
""",
        encoding="utf-8",
    )
    result = ingest_file(sess, csv_path)
    assert result.session.step in (Step.COLLECTING, Step.ASKING_WORKERS, Step.UNDERLOAD, Step.PLAN_READY)
    assert "нет приоритетов" in result.reply.lower() or "работников" in result.reply.lower()


def test_orchestrator_reports_when_target_workers_insufficient(tmp_path):
    sess = UserSession(session_id="test")
    # 100 items * 3600 sec each = 360_000 sec of work. One worker can only do 36_000 sec.
    sess.demands = {"task": 100.0}
    sess.norms = {"task": 3600.0}
    sess.priorities = {"task": 1}
    sess.target_workers = 1
    from factorydaemon.planner.orchestrator import run_planner

    result = run_planner(sess)
    assert "требует" in result.reply.lower()
    assert "10" in result.reply
    assert result.session.step in (Step.PLAN_READY, Step.UNDERLOAD)


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
