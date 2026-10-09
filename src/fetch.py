"""
Fetch the 支配下 roster of NPB teams (names, ages, positions, 1軍 stats) in one run.

Per team:
  Yahoo!スポーツナビ (https://baseball.yahoo.co.jp/npb/...)
    teams/<yid>/players       -- roster (position section, number, name, player_id)
    teams/<yid>/battingstats  -- this season's 1軍 batting (PA, OPS) for fielders
    teams/<yid>/pitchingstats -- this season's 1軍 pitching (G, GS, SV, HLD, IP) for pitchers
    player/<id>/top           -- every player: birth date, 投打, draft, career
  NPB.jp (https://npb.jp/bis/2026/stats/)
    idf1_<code>.html / idf2_<code>.html -- 1軍 / ファーム fielding games per position
Once per run:
  Yahoo npb/transfer -- 入退団情報: players who left (自由契約/引退/退団) are removed

Notes:
  - Stats come from team pages, not player pages: a player page shows 2軍 stats when a player
    has no 1軍 stats. Team pages are 1軍 only ("-" if none).
  - role (投手): GS/G >= 0.5 -> 先発, else SV >= SV_MIN_CLOSER -> 抑え, else 中継ぎ, G == 0 -> 不明.
  - position (野手): the position with the most games (1軍+2軍) among those allowed by the Yahoo
    category (内野手 -> 一/二/三塁手・遊撃手). Without any record: "内野手(不明)".
  - age: full age on AGE_REF_DATE (2027 opening day; provisional), computed from the birth date.
  - 育成 players (3-digit number such as 011) and coaches are excluded.

Usage:
  python src/fetch.py                     # all 12 teams (~30 min)
  python src/fetch.py g t                 # selected team codes only
  python src/fetch.py --departures-only   # re-apply 退団 removal to the existing files (no roster fetch)

Output (under $NPB_DATA/npb-draft-2026/rosters/):
  <code>_members.md  -- ## 投手 / ## 野手 tables
"""

import os
import re
import sys
import time
import unicodedata
from datetime import date
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
NPB_DATA = Path(os.environ.get("NPB_DATA", ROOT / "data"))
OUT_DIR = (NPB_DATA / "npb-draft-2026" if "NPB_DATA" in os.environ else NPB_DATA) / "rosters"

URL = "https://baseball.yahoo.co.jp/npb/teams/{yid}/players"
BATTING_URL = "https://baseball.yahoo.co.jp/npb/teams/{yid}/battingstats"
TRANSFER_URL = "https://baseball.yahoo.co.jp/npb/transfer"
PITCHING_URL = "https://baseball.yahoo.co.jp/npb/teams/{yid}/pitchingstats"
PLAYER_URL = "https://baseball.yahoo.co.jp/npb/player/{pid}/top"
NPB_FIELDING_URL = "https://npb.jp/bis/2026/stats/idf{n}_{code}.html"  # n=1: 1軍, n=2: ファーム
SLEEP_SEC = 2.0
AGE_REF_DATE = date(2027, 3, 26)  # 2027 opening day (provisional; update when the schedule is out)
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
            return BeautifulSoup(resp.content, "html.parser")  # bytes: let bs4 detect the charset (npb.jp omits it in headers)
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


def fetch_profile(pid):
    """Return birth date, age on AGE_REF_DATE, throws/bats, draft, career from a player page."""
    empty = {"birth": "", "age": "", "throws": "", "bats": "", "draft": "", "career": ""}
    try:
        soup = _get(PLAYER_URL.format(pid=pid))
    except requests.RequestException as e:
        print(f"  warning: profile fetch failed for {pid}: {e}")
        return empty
    prof = {dt.get_text(strip=True): dd.get_text(strip=True)
            for dt, dd in zip(soup.select(".bb-profile__title"), soup.select(".bb-profile__text"))}
    out = dict(empty)
    m = re.match(r"(\d+)年(\d+)月(\d+)日", prof.get("生年月日（満年齢）", ""))
    if m:
        b = date(*map(int, m.groups()))
        out["birth"] = b.isoformat()
        out["age"] = AGE_REF_DATE.year - b.year - ((AGE_REF_DATE.month, AGE_REF_DATE.day) < (b.month, b.day))
    m = re.match(r"([右左両])投([右左両])打", prof.get("投打", ""))
    if m:
        out["throws"], out["bats"] = m.groups()
    out["draft"] = prof.get("ドラフト年（順位）", "")
    out["career"] = prof.get("経歴", "")
    return out


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


# ---- fielding positions (NPB.jp) -------------------------------------------------------
POSITIONS = ("捕手", "一塁手", "二塁手", "三塁手", "遊撃手", "外野手")
ALLOWED = {"捕手": ("捕手",), "内野手": ("一塁手", "二塁手", "三塁手", "遊撃手"), "外野手": ("外野手",)}


def norm_name(name):
    """Normalize names across sites: NFKC, drop spaces, '*' marks and initials like 'T.'."""
    n = unicodedata.normalize("NFKC", name)
    n = re.sub(r"[\s*]", "", n)
    return re.sub(r"^[A-Za-z]\.", "", n)


def fetch_fielding_games(code):
    """Return {normalized name: {position: games}} summed over 1軍 and ファーム."""
    games = {}
    for n in (1, 2):
        soup = _get(NPB_FIELDING_URL.format(n=n, code=code))
        for h5 in soup.find_all("h5"):
            pos = h5.get_text(strip=True)
            table = h5.find_next("table")
            if pos not in POSITIONS or table is None:
                continue
            for tr in table.find_all("tr")[1:]:
                c = [x.get_text(strip=True) for x in tr.find_all(["th", "td"])]
                if len(c) >= 2 and c[1].isdigit():
                    d = games.setdefault(norm_name(c[0]), {})
                    d[pos] = d.get(pos, 0) + int(c[1])
        time.sleep(SLEEP_SEC)
    return games


def fielder_position(category, g):
    """Return (position label, 'pos games/pos games' string) from games per position."""
    allowed = {k: v for k, v in g.items() if k in ALLOWED[category] and v > 0}
    detail = "/".join(f"{k}{v}" for k, v in sorted(g.items(), key=lambda kv: -kv[1]) if v > 0)
    if allowed:
        return max(allowed, key=allowed.get), detail
    return (f"{category}(不明)" if category == "内野手" else category), detail


def _cell(x):
    return str(x).replace("|", "/")


def _md_table(cols, rows):
    lines = ["| " + " | ".join(h for h, _ in cols) + " |", "|" + "---|" * len(cols)]
    lines += ["| " + " | ".join(_cell(p.get(k, "")) for _, k in cols) + " |" for p in rows]
    return lines


PITCHER_COLS = [("player_id", "player_id"), ("number", "number"), ("name", "name"), ("birth", "birth"),
                ("age", "age"), ("throws", "throws"), ("bats", "bats"), ("role", "role"), ("auto", "auto"), ("G", "g"),
                ("GS", "gs"), ("SV", "sv"), ("HLD", "hld"), ("IP", "ip"), ("draft", "draft"),
                ("career", "career")]
FIELDER_COLS = [("player_id", "player_id"), ("number", "number"), ("name", "name"), ("birth", "birth"),
                ("age", "age"), ("position", "position"), ("auto", "auto"), ("positions", "positions"), ("throws", "throws"),
                ("bats", "bats"), ("PA", "pa"), ("OPS", "ops"), ("draft", "draft"), ("career", "career")]


def to_markdown(players, team, yid, excluded=()):
    pitchers = [p for p in players if p["section"] == PITCHER]
    fielders = [p for p in players if p["section"] != PITCHER]
    lines = [
        f"# {team} 支配下選手一覧 (2026)",
        "",
        f"- 取得日: {date.today().isoformat()}",
        f"- 取得元: {URL.format(yid=yid)} ほか (Yahoo!スポーツナビ, NPB.jp 守備成績)",
        f"- 人数: {len(players)} (投手 {len(pitchers)} / 野手 {len(fielders)})",
    ]
    if excluded:
        lines.append(f"{EXCLUDED_PREFIX} ({len(excluded)}名): " + ", ".join(excluded))
    lines += [
        f"- age: {AGE_REF_DATE.isoformat()} 時点の満年齢 (2027年開幕日は仮置き)",
        "- PA・OPS・G/GS/SV/HLD/IPは今季1軍の成績(球団別成績ページより。1軍出場なしは空欄/0)",
        "- positions: 守備位置別の出場試合数(1軍+ファーム)。position はその最多(内野手/捕手/外野手の区分内)。記録なしの内野手は「内野手(不明)」",
        "- auto: 自動判定した role / position。position・role を手で書き換えると、再取得しても引き継ぐ(auto と異なる値＝手動で書き換えた値)",
        "- 育成選手(背番号3桁)・監督コーチは除く",
        f"- role: 先発=GS/G>=0.5, 抑え=SV>={SV_MIN_CLOSER}(先発以外), 中継ぎ=その他, 不明=今季1軍登板なし",
        "",
        "## 投手",
        "",
        *_md_table(PITCHER_COLS, pitchers),
        "",
        "## 野手",
        "",
        *_md_table(FIELDER_COLS, fielders),
    ]
    return "\n".join(lines) + "\n"


YAHOO_TEAM_LABEL = {"g": "巨人", "t": "阪神", "db": "DeNA", "c": "広島", "d": "中日", "s": "ヤクルト",
                    "h": "ソフトバンク", "l": "西武", "e": "楽天", "m": "ロッテ", "f": "日本ハム", "b": "オリックス"}
EXCLUDED_PREFIX = "- 退団により除外"


def fetch_departures():
    """Return {team_code: {player_name: reason}} from the 入退団情報 page (状況 == 退団)."""
    soup = _get(TRANSFER_URL)
    out = {}
    for code, label in YAHOO_TEAM_LABEL.items():
        h = next((h for h in soup.find_all(["h2", "h3", "h4"]) if h.get_text(strip=True) == label), None)
        table = h.find_next("table") if h else None
        if table is None:
            print(f"  warning: no 入退団情報 table for {label}")
            continue
        out[code] = {}
        for tr in table.find_all("tr")[1:]:
            c = [x.get_text(strip=True) for x in tr.find_all(["th", "td"])]
            if len(c) >= 5 and c[1] == "退団":
                out[code][c[2].replace("※", "")] = c[4]
    return out


def exclude_departed(md, departed):
    """Remove rows of departed players from a team file text; update the counts and the 除外 line."""
    kept, removed = [], []
    for line in md.split("\n"):
        m = re.match(r"\| \d+ \| \S+ \| (.+?) \|", line)
        if m and m.group(1) in departed:
            removed.append(f"{m.group(1)}({departed[m.group(1)]})")
        else:
            kept.append(line)
    text = "\n".join(kept)
    old = re.search(rf"^{EXCLUDED_PREFIX} \(\d+名\): (.*)$", text, re.M)
    names = ([x for x in old.group(1).split(", ") if x] if old else []) + removed
    if old:
        text = text.replace(old.group(0) + "\n", "")
    pit_part, _, fld_part = text.partition("## 野手")
    n_p = len(re.findall(r"^\| \d+ \| ", pit_part, re.M))
    n_f = len(re.findall(r"^\| \d+ \| ", fld_part, re.M))
    text = re.sub(r"^- 人数: .*$", f"- 人数: {n_p + n_f} (投手 {n_p} / 野手 {n_f})", text, count=1, flags=re.M)
    if names:
        line = f"{EXCLUDED_PREFIX} ({len(names)}名): " + ", ".join(names)
        text = re.sub(r"^(- 人数: .*)$", lambda m: m.group(1) + "\n" + line, text, count=1, flags=re.M)
    return text, removed


def apply_departures(codes, departures):
    for code in codes:
        path = OUT_DIR / f"{code}_members.md"
        text, removed = exclude_departed(path.read_text(encoding="utf-8"), departures.get(code, {}))
        path.write_text(text, encoding="utf-8")
        print(f"{code:>2} 退団により除外: {len(removed)}名")


def load_manual_edits(path):
    """Return {player_id: value} for rows whose role/position was edited by hand (value != auto)."""
    if not path.exists():
        return {}
    out, cols = {}, None
    for line in path.read_text(encoding="utf-8").split("\n"):
        if line.startswith("## "):
            cols = None
        elif line.startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if cols is None:
                cols = cells
            elif not set(cells[0]) <= {"-"}:
                d = dict(zip(cols, cells))
                value = d.get("position") or d.get("role")
                if d.get("auto") and value and value != d["auto"]:
                    out[d["player_id"]] = value
    return out


def main(codes):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    departures = fetch_departures()
    for code in codes:
        yid, team = TEAMS[code]
        time.sleep(SLEEP_SEC)
        resp = requests.get(URL.format(yid=yid), headers=HEADERS, timeout=30)
        resp.raise_for_status()
        listed = parse_players(resp.text)
        departed = departures.get(code, {})
        players = [p for p in listed if p["name"] not in departed]
        excluded = [f"{p['name']}({departed[p['name']]})" for p in listed if p["name"] in departed]
        time.sleep(SLEEP_SEC)
        batting = _stats_table(BATTING_URL.format(yid=yid))
        time.sleep(SLEEP_SEC)
        pitching = _stats_table(PITCHING_URL.format(yid=yid))
        time.sleep(SLEEP_SEC)
        fielding = fetch_fielding_games(code)
        for p in players:
            key = (p["number"], p["name"])
            if p["section"] == PITCHER:
                p.update(pitcher_stats(pitching.get(key, {})))
            else:
                if key not in batting:
                    print(f"  warning: no row in battingstats for {p['name']} ({p['number']})")
                p.update(fielder_stats(batting.get(key, {})))
                p["position"], p["positions"] = fielder_position(p["section"], fielding.get(norm_name(p["name"]), {}))
            time.sleep(SLEEP_SEC)
            p.update(fetch_profile(p["player_id"]))
            if not p["birth"]:
                print(f"  warning: birth date not found for {p['name']} ({p['player_id']})")
        manual = load_manual_edits(OUT_DIR / f"{code}_members.md")
        for p in players:
            field = "role" if p["section"] == PITCHER else "position"
            p["auto"] = p[field]
            if p["player_id"] in manual:
                p[field] = manual[p["player_id"]]
        if manual:
            print(f"  carried over {sum(p['player_id'] in manual for p in players)} manual edits")
        unknown = [p["name"] for p in players if p["section"] == "内野手" and p["position"] == "内野手(不明)"]
        path = OUT_DIR / f"{code}_members.md"
        path.write_text(to_markdown(players, team, yid, excluded), encoding="utf-8")
        print(f"{code:>2} {team}: {len(players)} players (除外 {len(excluded)}) -> {path.name}; "
              f"内野手(不明): {len(unknown)}")


if __name__ == "__main__":
    args = sys.argv[1:]
    only_departures = "--departures-only" in args
    codes = [a for a in args if not a.startswith("--")] or list(TEAMS)
    if only_departures:
        apply_departures(codes, fetch_departures())
    else:
        main(codes)
