"""Tests for UserSession persistence and normalizer integration."""

from __future__ import annotations

import pandas as pd
import pytest

from factorydaemon.planner.session import UserSession


def test_session_defaults():
    sess = UserSession(session_id="test")
    assert sess.shift_hours == 10.0
    assert sess.max_positions_per_worker == 5
    assert sess.underload_threshold == 0.95


def test_target_workers_round_trip():
    sess = UserSession(session_id="test", target_workers=3)
    data = sess.to_dict()
    restored = UserSession.from_dict(data)
    assert restored.target_workers == 3


def test_update_demands_normalizes_position():
    sess = UserSession(session_id="test")
    df = pd.DataFrame({"позиция": ["л-43", "Л 43", "11В-11"], "количество": [10, 5, 2]})
    sess.update_demands(df, "позиция", "количество")
    assert "Л43" in sess.demands
    assert sess.demands["Л43"] == 15.0
    assert "11В-11" in sess.demands


def test_update_norms_normalizes_position():
    sess = UserSession(session_id="test")
    df = pd.DataFrame({"позиция": ["л-43"], "время": [120]})
    sess.update_norms(df, "позиция", "время")
    assert sess.norms == {"Л43": 120.0}


def test_update_priorities_normalizes_position():
    sess = UserSession(session_id="test")
    df = pd.DataFrame({"позиция": ["л-43"], "приоритет": [1]})
    sess.update_priorities(df, "позиция", "приоритет")
    assert sess.priorities == {"Л43": 1}


def test_missing_norms_tracked():
    sess = UserSession(session_id="test")
    sess.demands = {"A": 10}
    sess.norms = {}
    assert sess.missing_norms_positions == ["A"]
    # Session needs all three inputs before a final Excel plan.
    assert sess.is_ready_to_plan is False


def test_ready_to_plan_requires_all_inputs():
    sess = UserSession(session_id="test")
    assert sess.is_ready_to_plan is False
    sess.demands = {"A": 10}
    assert sess.is_ready_to_plan is False
    sess.norms = {"A": 5.0}
    assert sess.is_ready_to_plan is False
    sess.priorities = {"A": 1}
    assert sess.is_ready_to_plan is True

if __name__ == "__main__":
    pytest.main([__file__, "-v"])


def test_update_priorities_plan_file_sets_demands():
    sess = UserSession(session_id="test")
    df = pd.DataFrame({"позиция": ["A", "B", "C"], "количество": [100.0, 200.0, 300.0]})
    sess.update_priorities(df, "позиция", "количество", is_plan_file=True)
    assert sess.demands == {"A": 100.0, "B": 200.0, "C": 300.0}
    assert sess.priorities["A"] == 3
    assert sess.priorities["B"] == 2
    assert sess.priorities["C"] == 1


def test_update_priorities_default_uses_column_as_priority():
    sess = UserSession(session_id="test")
    df = pd.DataFrame({"позиция": ["A", "B", "C"], "приоритет": [1, 2, 3]})
    sess.update_priorities(df, "позиция", "приоритет")
    assert sess.priorities == {"A": 1, "B": 2, "C": 3}
    assert sess.demands == {}
