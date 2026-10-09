"""
Fetch the 支配下 roster of NPB teams from Yahoo!スポーツナビ.

Pages per team (https://baseball.yahoo.co.jp/npb/...):
  teams/<yid>/players       -- roster (position section, number, name, player_id)
  teams/<yid>/battingstats  -- this season's 1軍 batting (PA, OPS) for fielders
  teams/<yid>/pitchingstats -- this season's 1軍 pitching (G, GS, SV, HLD, IP) for pitchers
  player/<id>/top           -- pitchers only, for the throwing hand (右/左)

Why not the player pages for stats: when a player has no 1軍 stats, the player page
shows 2軍 stats instead, so team stats pages are used (1軍 only; "-" if none).

Role (先発/中継ぎ/抑え/不明) is derived: GS/G >= 0.5 -> 先発, else SV >= SV_MIN_CLOSER -> 抑え,
else 中継ぎ, G == 0 -> 不明. 育成 players (3-digit number such as 011) and coaches are excluded.

Usage:
  python src/fetch.py          # all 12 teams
  python src/fetch.py g t      # selected team codes only

Output (under $NPB_DATA/npb-draft-2026/rosters/):
  <code>_members.md  -- ## 投手 / ## 野手 tables (pitchers: throws/role/G/GS/SV/HLD/IP; fielders: position/PA/OPS)
"""

import os
import re
import sys
import time
from datetime import date
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
NPB_DATA = Path(os.environ.get("NPB_DATA", ROOT / "data"))
OUT_DIR = (NPB_DATA / "npb-draft-2026" if "NPB_DATA" in os.environ else NPB_DATA) / "rosters"

URL = "https://baseball.yahoo.co.jp/npb/teams/{yid}/players"
BATTING_URL = "https://baseball.yahoo.co.jp/npb/teams/{yid}/battingstats"
PITCHING_URL = "https://baseball.yahoo.co.jp/npb/teams/{yid}/pitchingstats"
PLAYER_URL = "https://baseball.yahoo.co.jp/npb/player/{pid}/top"
SLEEP_SEC = 2.0
HEADERS = {"User-Agent": "Mozilla/5.0"}

# code -> (yahoo team id, team name)
TEAMS = {
    "g":  (1,   "読売ジャイアンツ"),
    "t":  (5,   "阪神タイガース"),
    "db": (3,   "横浜DeNAベイスターズ"),
    "c":  (6,   "広島東洋カープ"),
    "d":  (4,   "中日ドラゴンズ"),
    "s":  (2,   "東京ヤクルトスワローズ"),
    "h":  (12,  "福岡ソフトバンクホークス"),
    "l":  (7,   "埼玉西武ライオンズ"),
    "e":  (376, "東北楽天ゴールデンイーグルス"),
    "m":  (9,   "千葉ロッテマリーンズ"),
    "f":  (8,   "北海道日本ハムファイターズ"),
    "b":  (11,  "オリックス・バファローズ"),
}

PITCHER = "投手"
FIELDERS = ("捕手", "内野手", "外野手")


def parse_players(html):
    """Return a list of dicts (section, number, name, player_id) for 支配下 players."""
    soup = BeautifulSoup(html, "html.parser")
    players = []
    for sec in soup.select("section.bb-modCommon01"):
        h = sec.select_one("h2")
        section = h.get_text(strip=True) if h else ""
        if section != PITCHER and section not in FIELDERS:
            continue  # skips 監督・コーチ
        for li in sec.select("li.bb-playerList__item"):
            a = li.select_one("a.bb-playerList__link")
            m = re.search(r"/player/(\d+)/", a["href"]) if a else None
            number = li.select_one(".bb-playerList__number").get_text(strip=True)
            if m is None or len(number) >= 3:  # 育成 = 3-digit number
                continue
            players.append({
                "section": section,
                "number": number,
                "name": li.select_one(".bb-playerList__name").get_text(strip=True),
                "player_id": m.group(1),
            })
    return players


SV_MIN_CLOSER = 5  # saves needed to be labelled 抑え (non-starters)


def _int(x):
    return int(x) if x.isdigit() else 0


def classify_role(g, gs, sv):
    """先発 / 抑え / 中継ぎ from this season's 1軍 stats; 不明 if no appearances."""
    if g == 0:
        return "不明"
    if gs / g >= 0.5:
        return "先発"
    if sv >= SV_MIN_CLOSER:
        return "抑え"
    return "中継ぎ"


def _get(url, retries=3):
    """GET + parse; retry with a growing wait on server errors / network failures."""
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=30)
            resp.raise_for_status()
            return BeautifulSoup(resp.text, "html.parser")
        except requests.RequestException as e:
            if attempt == retries:
                raise
            print(f"  retry {attempt}/{retries - 1} after error: {e}")
            time.sleep(SLEEP_SEC * 5 * attempt)


def _stats_table(url):
    """Return {(number, name): {column: value}} from a team stats page (1軍 only)."""
    tbl = _get(url).find("table")
    rows = [[c.get_text(strip=True) for c in tr.find_all(["th", "td"])] for tr in tbl.find_all("tr")]
    header = [h.replace("\u3000", "") for h in rows[0]]
    out = {}
    for r in rows[1:]:
        d = dict(zip(header, r))
        out[(d["背番号"], d["選手名"])] = d
    return out


def fetch_throws(pid):
    """Return '右' / '左' / '両' from the 投打 field of a player page ('' if unavailable)."""
    soup = _get(PLAYER_URL.format(pid=pid))
    for dt, dd in zip(soup.select(".bb-profile__title"), soup.select(".bb-profile__text")):
        if dt.get_text(strip=True) == "投打":
            m = re.match(r"([右左両])投", dd.get_text(strip=True))
            return m.group(1) if m else ""
    return ""


def pitcher_stats(d):
    g, gs = _int(d.get("登板", "")), _int(d.get("先発", ""))
    sv, hld = _int(d.get("セーブ", "")), _int(d.get("ホールド", ""))
    return {"g": g, "gs": gs, "sv": sv, "hld": hld, "ip": d.get("投球回", "-"),
            "role": classify_role(g, gs, sv)}


def fielder_stats(d):
    pa, ops = d.get("打席", ""), d.get("ＯＰＳ", "")
    if not pa.isdigit() or int(pa) == 0:
        return {"pa": "", "ops": ""}
    return {"pa": pa, "ops": ops if ops != "-" else ""}


def _fielder_table(rows):
    lines = ["| player_id | number | name | position | PA | OPS |", "|---|---|---|---|---|---|"]
    lines += [f"| {p['player_id']} | {p['number']} | {p['name']} | {p['section']} | {p['pa']} | {p['ops']} |"
              for p in rows]
    return lines


def _table(rows, with_throws=False):
    if with_throws:
        lines = ["| player_id | number | name | throws | role | G | GS | SV | HLD | IP |",
                 "|---|---|---|---|---|---|---|---|---|---|"]
        lines += [f"| {p['player_id']} | {p['number']} | {p['name']} | {p['throws']} | {p['role']} "
                  f"| {p['g']} | {p['gs']} | {p['sv']} | {p['hld']} | {p['ip']} |" for p in rows]
    else:
        lines = ["| player_id | number | name |", "|---|---|---|"]
        lines += [f"| {p['player_id']} | {p['number']} | {p['name']} |" for p in rows]
    return lines


def to_markdown(players, team, yid):
    pitchers = [p for p in players if p["section"] == PITCHER]
    fielders = [p for p in players if p["section"] != PITCHER]
    lines = [
        f"# {team} 支配下選手一覧 (2026)",
        "",
        f"- 取得日: {date.today().isoformat()}",
        f"- 取得元: {URL.format(yid=yid)}",
        f"- 人数: {len(players)} (投手 {len(pitchers)} / 野手 {len(fielders)})",
        "- PA・OPS・G/GS/SV/HLD/IPは今季1軍の成績(球団別成績ページより。1軍出場なしは空欄/0)",
        "- 育成選手(背番号3桁)・監督コーチは除く",
        f"- role: 先発=GS/G>=0.5, 抑え=SV>={SV_MIN_CLOSER}(先発以外), 中継ぎ=その他, 不明=今季1軍登板なし",
        "",
        "## 投手",
        "",
        *_table(pitchers, with_throws=True),
        "",
        "## 野手",
        "",
        *_fielder_table(fielders),
    ]
    return "\n".join(lines) + "\n"


def main(codes):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for i, code in enumerate(codes):
        yid, team = TEAMS[code]
        resp = requests.get(URL.format(yid=yid), headers=HEADERS, timeout=30)
        resp.raise_for_status()
        players = parse_players(resp.text)
        time.sleep(SLEEP_SEC)
        batting = _stats_table(BATTING_URL.format(yid=yid))
        time.sleep(SLEEP_SEC)
        pitching = _stats_table(PITCHING_URL.format(yid=yid))
        for p in players:
            key = (p["number"], p["name"])
            if p["section"] == PITCHER:
                p.update(pitcher_stats(pitching.get(key, {})))
                time.sleep(SLEEP_SEC)
                p["throws"] = fetch_throws(p["player_id"])
                if not p["throws"]:
                    print(f"  warning: throws not found for {p['name']} ({p['player_id']})")
            else:
                if key not in batting:
                    print(f"  warning: no row in battingstats for {p['name']} ({p['number']})")
                p.update(fielder_stats(batting.get(key, {})))
        path = OUT_DIR / f"{code}_members.md"
        path.write_text(to_markdown(players, team, yid), encoding="utf-8")
        print(f"{code:>2} {team}: {len(players)} players -> {path.name}")
        if i < len(codes) - 1:
            time.sleep(SLEEP_SEC)


if __name__ == "__main__":
    main(sys.argv[1:] or list(TEAMS))
