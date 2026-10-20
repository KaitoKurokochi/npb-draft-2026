import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import build_view  # noqa: E402


def player(**kw):
    return {"kind": "投手", "role": "先発", "auto": "先発", "throws": "左", "leaving": False, **kw}


def test_leaving_player_goes_to_the_unsplit_leaving_column():
    p = player(leaving=True)
    assert build_view.column_of(p) == build_view.LEAVING
    assert build_view.column_hand(p) is None
    assert build_view.hand_of(p) == "左"  # the real hand is kept so the player can be dragged back


def test_set_leaving_adds_and_removes(tmp_path, monkeypatch):
    monkeypatch.setattr(build_view, "LEAVING_PATH", tmp_path / "leaving.json")
    build_view.set_leaving("a", True)
    build_view.set_leaving("b", True)
    build_view.set_leaving("a", False)
    assert build_view.load_json(build_view.LEAVING_PATH, []) == ["b"]


def test_set_need_toggles_per_team_and_rejects_unknowns(tmp_path, monkeypatch):
    monkeypatch.setattr(build_view, "NEEDS_PATH", tmp_path / "needs.json")
    assert build_view.set_need("g", "捕手", True) == (True, "ok")
    build_view.set_need("g", "先発(左)", True)
    build_view.set_need("t", "捕手", True)
    build_view.set_need("g", "捕手", False)
    assert build_view.load_json(build_view.NEEDS_PATH, {}) == {"g": ["先発(左)"], "t": ["捕手"]}
    assert build_view.set_need("zz", "捕手", True)[0] is False
    assert build_view.set_need("g", "投手", True)[0] is False
