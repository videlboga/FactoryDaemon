"""Tests for planner/session.py."""

from __future__ import annotations

import pandas as pd

from factorydaemon.planner.session import Step, UserSession


def _demand_df() -> pd.DataFrame:
    return pd.DataFrame({"Номенклатура": ["Л43", "10"], "Количество": [1200, 1850]})


def _norms_df() -> pd.DataFrame:
    return pd.DataFrame({"Деталь": ["Л43", "10"], "Сек/шт": [20.0, 10.0]})


def _priorities_df() -> pd.DataFrame:
    return pd.DataFrame({"Позиция": ["Л43", "10"], "Приоритет": [1, 2]})


def test_session_starts_collecting():
    sess = UserSession(session_id="test-1")
    assert sess.step == Step.COLLECTING
    assert sess.is_ready_to_plan is False


def test_session_extracts_demands():
    sess = UserSession(session_id="test-1")
    sess.update_demands(_demand_df(), "Номенклатура", "Количество")
    assert sess.demands == {"Л43": 1200.0, "10": 1850.0}
    assert sess.missing_norms_positions == ["Л43", "10"]


def test_session_ready_to_plan():
    sess = UserSession(session_id="test-1")
    sess.update_demands(_demand_df(), "Номенклатура", "Количество")
    sess.update_norms(_norms_df(), "Деталь", "Сек/шт")
    sess.update_priorities(_priorities_df(), "Позиция", "Приоритет")
    assert sess.is_ready_to_plan is True


def test_session_serialization():
    sess = UserSession(session_id="test-1")
    sess.update_demands(_demand_df(), "Номенклатура", "Количество")
    data = sess.to_dict()
    restored = UserSession.from_dict(data)
    assert restored.demands == sess.demands
    assert restored.step == sess.step
