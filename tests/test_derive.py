import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import derive  # noqa: E402


def test_age_on_opening_day():
    assert derive.age_on("2002-03-11") == 25
    assert derive.age_on("2002-03-27") == 24   # birthday after 2027-03-26
    assert derive.age_on(None) is None


def test_innings_and_lines():
    assert derive.innings(160) == "53.1"
    rows = [{"g": 18, "gs": 16, "w": 5, "l": 6, "sv": 0, "hold": 0, "outs": 300, "er": 40},
            {"g": 10, "gs": None, "w": 0, "l": 1, "sv": 0, "hold": 2, "outs": 30, "er": 5}]
    line = derive.pitching_line(rows)
    assert (line["g"], line["gs"], line["outs"]) == (28, 16, 330)
    assert abs(line["era"] - 45 * 27 / 330) < 1e-9


def test_pitcher_role():
    assert derive.pitcher_role({"g": 10, "gs": 5, "sv": 0}) == "先発"
    assert derive.pitcher_role({"g": 40, "gs": 0, "sv": 22}) == "抑え"
    assert derive.pitcher_role({"g": 40, "gs": 0, "sv": 4}) == "中継ぎ"
    assert derive.pitcher_role({"g": 10, "gs": None, "sv": 0}) == "不明"   # start count unavailable
    assert derive.pitcher_role(None) == "不明"


def test_fielder_position_stays_within_category():
    pos, text = derive.fielder_position("内野手", {"外野手": 55, "一塁手": 40, "三塁手": 1})
    assert pos == "一塁手" and text == "外野手55/一塁手40/三塁手1"
    assert derive.fielder_position("内野手", {})[0] == "内野手(不明)"
    assert derive.fielder_position("外野手", {})[0] == "外野手"


def test_regular_flag_uses_auto_role():
    assert derive.is_regular("投手", "先発", 25, 20, None)
    assert not derive.is_regular("投手", "先発", 25, 19, None)
    assert derive.is_regular("投手", "中継ぎ", 40, 0, None)
    assert derive.is_regular("野手", "", None, None, 250)


def test_stat_cells_match_headers():
    pit = {"g": 40, "gs": 0, "w": 3, "l": 2, "sv": 1, "hold": 20, "outs": 160, "era": 2.5}
    assert len(derive.pitching_cells(pit)) == len(derive.PITCHING_HEADERS)
    assert derive.pitching_cells(pit)[-2:] == ["53.1", "2.50"]
    bat = {"g": 100, "pa": 400, "avg": 0.2851, "hr": 12, "rbi": 40, "sb": 5, "ops": 0.7}
    assert len(derive.batting_cells(bat)) == len(derive.BATTING_HEADERS)
    assert derive.batting_cells(bat)[2] == ".285" and derive.batting_cells(bat)[-1] == ".700"


def test_no_appearance_has_no_cells():
    assert derive.pitching_cells(None) is None
    assert derive.batting_cells({"pa": 0}) is None
