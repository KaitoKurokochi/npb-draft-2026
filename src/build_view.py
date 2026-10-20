"""
Build a browser-viewable roster chart (age x position) for each of the 12 teams.

Reads : $NPB_DATA/npb_scraper/npb.sqlite            (npb_scraper's database, read-only)
        $NPB_DATA/npb-draft-2026/overrides.json     (manual position / role changes)
        $NPB_DATA/npb-draft-2026/labels.json        (「まだ数年使える」 labels)
Writes: $NPB_DATA/npb-draft-2026/roster_view.html   (and overrides.json / labels.json in --serve mode)

Rows   : age on the 2027 opening day (~19, 20, ..., 34, 35~)
Columns: 先発(右) / 先発(左) / 中継ぎ・抑え(右) / 中継ぎ・抑え(左) / 捕手 / 一塁手 / 二塁手 / 三塁手 / 遊撃手 / 外野手 (+ 内野手(不明) if any)
Cells  : '<surname>#<number>' (hover for a popup: draft, career, 2026 / 2025 stats); bright = regular (thresholds in derive.py), dim = others.
         Pitchers sorted by innings, fielders by plate appearances (2026, 1軍).

Moving players (planned future position):
  Dragging a player in --serve mode stores a manual `position` (fielders) or `role` (pitchers) for that
  player_id in overrides.json; moving back to the automatic value removes it. A player can only be moved to
  another column of the same kind and throwing hand (pitchers: 先発/中継ぎ・抑え, fielders: 捕手/一塁手/二塁手/
  三塁手/遊撃手/外野手) within the same age row.

Labels (まだ数年使える):
  Clicking a player name in --serve mode toggles a green label, stored in labels.json. The static page shows
  the labels but cannot change them.

Usage:
  python3 src/build_view.py            # write a static roster_view.html (not editable)
  python3 src/build_view.py --serve    # editable page at http://127.0.0.1:8765 (drag to move players)
"""

import html
import json
import os
import sys
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import derive  # noqa: E402
import npb_db  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
NPB_DATA = Path(os.environ.get("NPB_DATA", ROOT / "data"))
DATA_ROOT = NPB_DATA / "npb-draft-2026" if "NPB_DATA" in os.environ else NPB_DATA
OUT_PATH = DATA_ROOT / "roster_view.html"
LABELS_PATH = DATA_ROOT / "labels.json"        # [player_id, ...]
OVERRIDES_PATH = DATA_ROOT / "overrides.json"  # {player_id: {"position": ...} | {"role": ...}}
LEAVING_PATH = DATA_ROOT / "leaving.json"      # [player_id, ...] expected to leave the team
NEEDS_PATH = DATA_ROOT / "needs.json"          # {team code: {column label: level}} positions that need reinforcement
NEED_LEVELS = ("future", "now")                # yellow: needed in the future; red: needs an immediate contributor
PORT = 8765

TEAMS = {  # code -> name, in display order
    "g": "読売ジャイアンツ", "t": "阪神タイガース", "db": "横浜DeNAベイスターズ", "c": "広島東洋カープ",
    "d": "中日ドラゴンズ", "s": "東京ヤクルトスワローズ", "h": "福岡ソフトバンクホークス", "l": "埼玉西武ライオンズ",
    "e": "東北楽天ゴールデンイーグルス", "m": "千葉ロッテマリーンズ", "f": "北海道日本ハムファイターズ", "b": "オリックス・バファローズ",
}
TEAM_ORDER = list(TEAMS)
AGE_ROWS = ["~19"] + [str(a) for a in range(20, 35)] + ["35~"]

LEAVING = "退団予定"  # column key; holds players of any kind, so it has no throwing hand

# (column key, label, throwing hand or None). Pitcher columns are split by throwing hand.
COLUMNS = [
    ("先発", "先発(右)", "右"),
    ("先発", "先発(左)", "左"),
    ("救援", "中継ぎ・抑え(右)", "右"),
    ("救援", "中継ぎ・抑え(左)", "左"),
    ("捕手", "捕手", None),
    ("一塁手", "一塁手", None),
    ("二塁手", "二塁手", None),
    ("三塁手", "三塁手", None),
    ("遊撃手", "遊撃手", None),
    ("内野手(不明)", "内野手(不明)", None),
    ("外野手", "外野手", None),
    (LEAVING, "退団予定", None),
]
PIT_COLS = ("先発", "救援")
FLD_COLS = ("捕手", "一塁手", "二塁手", "三塁手", "遊撃手", "外野手")


def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def save_json(path, data) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def to_view_player(raw: dict, override: dict, leaving: bool = False) -> dict:
    """Combine a database player with derived values and the manual override (if any)."""
    kind = "投手" if raw["category"] == "投手" else "野手"
    cur, prev = npb_db.SEASON, npb_db.PREV_SEASON
    p = {"kind": kind, "player_id": raw["player_id"], "name": raw["name"], "number": raw["number"] or "",
         "age": derive.age_on(raw["birth_date"]), "birth": raw["birth_date"] or "",
         "throws": raw["throws"] or "", "bats": raw["bats"] or "",
         "draft": raw["draft"] or "", "career": raw["career"] or "", "leaving": leaving}
    if kind == "投手":
        now, before = (derive.pitching_line(raw["pitching"].get(y, [])) for y in (cur, prev))
        p["auto"] = derive.pitcher_role(now)
        p["role"] = override.get("role", p["auto"])
        p["sort"] = (now or {}).get("outs") or 0
        p["regular"] = derive.is_regular(kind, p["auto"], (now or {}).get("g"), (now or {}).get("gs"), None)
        p["headers"] = derive.PITCHING_HEADERS
        p["seasons"] = [(cur, derive.pitching_cells(now)), (prev, derive.pitching_cells(before))]
    else:
        now, before = (derive.batting_line(raw["batting"].get(y, [])) for y in (cur, prev))
        games = {}
        for r in raw["fielding"]:
            games[r["position"]] = games.get(r["position"], 0) + (r["g"] or 0)
        p["auto"], p["positions"] = derive.fielder_position(raw["category"], games)
        p["position"] = override.get("position", p["auto"])
        p["sort"] = (now or {}).get("pa") or 0
        p["regular"] = derive.is_regular(kind, "", None, None, (now or {}).get("pa"))
        p["headers"] = derive.BATTING_HEADERS
        p["seasons"] = [(cur, derive.batting_cells(now)), (prev, derive.batting_cells(before))]
    return p


def load_view_players(conn, code: str, overrides: dict, leaving=frozenset()) -> list[dict]:
    return [to_view_player(raw, overrides.get(raw["player_id"], {}), raw["player_id"] in leaving)
            for raw in npb_db.load_team(conn, code)]


def column_of(p):
    if p["leaving"]:
        return LEAVING
    if p["kind"] == "投手":
        return "先発" if p["role"] == "先発" else "救援"  # 中継ぎ / 抑え / 不明 -> 救援
    return p["position"]


def column_hand(p):
    """Throwing hand that splits the pitcher columns; the 退団予定 column is not split."""
    return None if p["leaving"] else hand_of(p)


def auto_column(p):
    return ("先発" if p["auto"] == "先発" else "救援") if p["kind"] == "投手" else p["auto"]


def hand_of(p):
    """Throwing hand used for the pitcher columns (anything but 左 counts as 右); None for fielders."""
    return None if p["kind"] != "投手" else ("左" if p["throws"] == "左" else "右")


def age_label(age):
    if age is None:
        return "年齢不明"
    return "~19" if age <= 19 else "35~" if age >= 35 else str(age)


def label(p):
    """'<surname>#<number>'."""
    return f"{html.escape(p['name'].split()[0])}#{html.escape(p['number'])}"


def popup_html(p, moved_note=""):
    """Contents of the hover popup (already escaped); drafts and careers are empty until npb_scraper's profile stage has run."""
    e = html.escape
    age = f"{p['age']}歳" if p["age"] is not None else ""
    sub = " ・ ".join(x for x in (f"#{p['number']}", p["birth"], age, f"{p['throws']}投{p['bats']}打") if x)
    kind = f"役割 {p['role']}" if p["kind"] == "投手" else f"守備 {p['positions'] or p['position']}"
    facts = [("ドラフト", p["draft"]), ("経歴", p["career"])]
    rows = "".join(f"<tr><th>{y}</th>" + (
        "".join(f"<td>{e(str(c))}</td>" for c in cells) if cells else
        f"<td colspan={len(p['headers'])} class=mute>1軍出場なし</td>") + "</tr>" for y, cells in p["seasons"])
    return (f"<b>{e(p['name'])}</b> <span class=mute>{e(sub)}</span><br><span class=mute>{e(kind)}</span>"
            + "".join(f"<br>{k}: {e(v)}" for k, v in facts if v)
            + f"<table><tr><th></th>{''.join(f'<th>{h}</th>' for h in p['headers'])}</tr>{rows}</table>"
            + (f"<br><span class=mute>{e(moved_note)}</span>" if moved_note else ""))


def set_label(pid, on):
    """Add / remove one player_id in labels.json; return (ok, message)."""
    if not pid:
        return False, "no player_id"
    labels = set(load_json(LABELS_PATH, []))
    labels.add(pid) if on else labels.discard(pid)
    save_json(LABELS_PATH, sorted(labels))
    return True, "ok"


def set_need(code, column, level):
    """Set (level in NEED_LEVELS) or clear (None) the reinforcement level of one column label of one team; return (ok, message)."""
    if code not in TEAMS or column not in {lab for _, lab, _ in COLUMNS}:
        return False, "unknown team or column"
    if level is not None and level not in NEED_LEVELS:
        return False, f"unknown level {level}"
    needs = load_json(NEEDS_PATH, {})
    team = needs.setdefault(code, {})
    if level is None:
        team.pop(column, None)
    else:
        team[column] = level
    save_json(NEEDS_PATH, needs)
    return True, "ok"


def set_leaving(pid, on):
    """Add / remove one player_id in leaving.json; return (ok, message)."""
    if not pid:
        return False, "no player_id"
    leaving = set(load_json(LEAVING_PATH, []))
    leaving.add(pid) if on else leaving.discard(pid)
    save_json(LEAVING_PATH, sorted(leaving))
    return True, "ok"


def apply_move(code, pid, to):
    """Move a player to a column: 退団予定 is stored in leaving.json, any other column as the manual
    position / role in overrides.json (the player leaves 退団予定 first); return (ok, message)."""
    if code not in TEAMS:
        return False, "unknown team"
    with npb_db.connect() as conn:
        p = next((x for x in load_view_players(conn, code, {}) if x["player_id"] == pid), None)
    if p is None:
        return False, "unknown player"
    if to == LEAVING:
        return set_leaving(pid, True)
    if to not in (PIT_COLS if p["kind"] == "投手" else FLD_COLS):
        return False, f"{to} is not allowed for {p['kind']}"
    set_leaving(pid, False)
    field = "role" if p["kind"] == "投手" else "position"
    if p["kind"] == "投手":
        # moving back to the auto column restores `auto`; otherwise 先発 or 中継ぎ
        new = p["auto"] if auto_column(p) == to else ("先発" if to == "先発" else "中継ぎ")
    else:
        new = to
    overrides = load_json(OVERRIDES_PATH, {})
    if new == p["auto"]:
        overrides.pop(pid, None)
    else:
        overrides[pid] = {field: new}
    save_json(OVERRIDES_PATH, overrides)
    return True, "ok"


def player_div(code, p, editable, labels=frozenset()):
    orig, now = auto_column(p), column_of(p)
    moved = orig != now
    tip = popup_html(p, f"手動で移動: {orig} → {now}" if moved else "")
    cls = f"p {'hi' if p['regular'] else 'lo'}{' moved' if moved else ''}{' keep' if p['player_id'] in labels else ''}"
    drag = " draggable='true'" if editable else ""
    return (f"<div class='{cls}'{drag} data-id='{html.escape(p['player_id'])}' data-kind='{p['kind']}' data-throws='{hand_of(p) or ''}' "
            f"data-tip='{html.escape(tip, quote=True)}'>{label(p)}</div>")


def need_class(level):
    return f" need-{level}" if level else ""


def build_team(code, players, editable, labels=frozenset(), needs=None):
    needs = needs or {}
    grid, counts = {}, {}
    for p in sorted(players, key=lambda p: -p["sort"]):
        row, col = age_label(p["age"]), (column_of(p), column_hand(p))
        grid.setdefault(row, {}).setdefault(col, []).append(p)
        counts[col] = counts.get(col, 0) + 1
    rows = AGE_ROWS + (["年齢不明"] if "年齢不明" in grid else [])
    cols = [(k, lab, h) for k, lab, h in COLUMNS if k != "内野手(不明)" or counts.get((k, h))]

    out = [f"<h2>{html.escape(TEAMS[code])} <small>{len(players)}名</small></h2>", f"<div class=wrap><table data-code='{code}'>"]
    out.append("<thead><tr><th>年齢</th>" + "".join(
        f"<th class='col{need_class(needs.get(lab))}' data-label='{html.escape(lab)}'>{html.escape(lab)}<span class=n>{counts.get((k, h), 0)}</span></th>" for k, lab, h in cols) + "<th>計</th></tr></thead><tbody>")
    for r in rows:
        cells, total = [], 0
        for k, lab, h in cols:
            ps = grid.get(r, {}).get((k, h), [])
            total += len(ps)
            cells.append(f"<td data-col='{k}' data-throws='{h or ''}'{f" class='{need_class(needs.get(lab)).strip()}'" if lab in needs else ''}>" + "".join(player_div(code, p, editable, labels) for p in ps) + "</td>")
        out.append(f"<tr class='{'empty' if total == 0 else ''}'><th>{r}</th>{''.join(cells)}<td class=tot>{total or ''}</td></tr>")
    out.append("</tbody></table></div>")
    return "\n".join(out)


PAGE = """<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>NPB 2026 現状戦力</title>
<style>
:root{--bg:#fff;--fg:#1c1f24;--mute:#6b7280;--line:#e2e5ea;--head:#f3f4f6;--keep:#15803d;--acc:#1d4ed8;--accbg:#e8efff;--pit:var(--fg);--fld:#1c1f24}
@media (prefers-color-scheme:dark){:root{--bg:#14161a;--fg:#e6e8ec;--mute:#9aa1ad;--line:#2d3139;--head:#1d2025;--keep:#4ade80;--acc:#8ab4ff;--accbg:#1f2a44;--pit:var(--fg);--fld:#e6e8ec}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 -apple-system,"Hiragino Sans",sans-serif}
header{padding:16px;border-bottom:1px solid var(--line)}h1{margin:0 0 4px;font-size:18px}
p.note{margin:0;color:var(--mute);font-size:12px}
nav{display:flex;flex-wrap:wrap;gap:6px;padding:12px 16px;position:sticky;top:0;background:var(--bg);border-bottom:1px solid var(--line);z-index:2}
nav button{border:1px solid var(--line);background:var(--bg);color:var(--fg);padding:4px 10px;border-radius:6px;cursor:pointer;font:inherit}
nav button[aria-selected=true]{background:var(--accbg);border-color:var(--acc);color:var(--acc)}
section{display:none;padding:16px}section.on{display:block}
h2{margin:0 0 10px;font-size:16px}h2 small{color:var(--mute);font-weight:400}
.wrap{overflow-x:auto}table{border-collapse:collapse;min-width:100%}
th,td{border:1px solid var(--line);padding:4px 8px;vertical-align:top;text-align:left;white-space:nowrap}
thead th{background:var(--head);position:sticky;top:0}tbody th{background:var(--head);text-align:center}
th .n{color:var(--mute);font-weight:400;margin-left:4px;font-size:12px}
tr.empty th{color:var(--mute);font-weight:400}.tot{text-align:center;color:var(--mute)}
.p{cursor:default}.p[draggable=true]{cursor:grab}td.ok{background:var(--accbg)}.p.dragging{opacity:.4}#msg{color:var(--acc);font-size:12px}.p.hi{color:var(--fg);font-weight:600}.p.lo{color:var(--mute)}.p.keep{color:var(--keep);font-weight:600}.p[draggable=true]:hover{text-decoration:underline}
:root{--future:#fde68a;--futurefg:#713f12;--now:#fca5a5;--nowfg:#7f1d1d}@media (prefers-color-scheme:dark){:root{--future:#5b4a0a;--futurefg:#fde68a;--now:#7f1d1d;--nowfg:#fecaca}}
thead th.need-future{background:var(--future);color:var(--futurefg)}td.need-future{background:color-mix(in srgb,var(--future) 35%,transparent)}
thead th.need-now{background:var(--now);color:var(--nowfg)}td.need-now{background:color-mix(in srgb,var(--now) 35%,transparent)}th.col{cursor:default}
#tip{position:fixed;display:none;z-index:10;max-width:420px;padding:8px 10px;background:var(--bg);color:var(--fg);border:1px solid var(--line);border-radius:6px;box-shadow:0 4px 14px rgba(0,0,0,.25);font-size:12px;pointer-events:none;white-space:normal}
#tip .mute,#tip td.mute{color:var(--mute)}#tip table{margin-top:6px;min-width:0}#tip th,#tip td{padding:1px 6px;text-align:right;font-weight:400}#tip th{background:none;position:static}
</style></head><body>
<header><h1>NPB 2026 現状戦力</h1>
<p class=note>行 = 2027年開幕時の年齢（仮: 3/26時点）／ 名字#背番号／ 明るい文字 = 先発GS≥__GS__ ・ 救援G≥__G__ ・ 野手PA≥__PA__（それ以外は暗い）／ カーソルでポップアップ（ドラフト・経歴・2026/2025の成績）／ 緑 = まだ数年使えそう（名前クリックで付け外し）／ 列の見出しをクリックで 黄（将来的に補強が必要）→ 赤（即戦力の補強が必要）→ 解除／ 退団者（自由契約・引退）と育成選手は除く ／ 生成日 __DATE__<br>__EDITNOTE__</p><p id=msg></p></header>
<nav id=tabs></nav>
<div id=tip></div>
__SECTIONS__
<script>
const names=__NAMES__;const tabs=document.getElementById('tabs');const secs=[...document.querySelectorAll('section')];
function show(i){secs.forEach((s,j)=>s.classList.toggle('on',i===j));[...tabs.children].forEach((b,j)=>b.setAttribute('aria-selected',i===j));history.replaceState(null,'','#'+i)}
names.forEach((n,i)=>{const b=document.createElement('button');b.textContent=n;b.onclick=()=>show(i);tabs.appendChild(b)});
show(Math.min(parseInt(location.hash.slice(1))||0,names.length-1));
const tip=document.getElementById('tip');
document.addEventListener('mouseover',e=>{const d=e.target.closest('.p');if(!d){tip.style.display='none';return}
  tip.innerHTML=d.dataset.tip;tip.style.display='block';const r=d.getBoundingClientRect();
  tip.style.left=Math.max(4,Math.min(r.left,innerWidth-tip.offsetWidth-4))+'px';
  tip.style.top=(r.bottom+tip.offsetHeight+8>innerHeight?Math.max(4,r.top-tip.offsetHeight-4):r.bottom+4)+'px'});
document.addEventListener('dragstart',()=>{tip.style.display='none'});
const EDIT=__EDIT__;
if(EDIT){
  const PIT=['先発','救援'],FLD=['捕手','一塁手','二塁手','三塁手','遊撃手','外野手'];
  const NEED=[null,'future','now'];
  document.addEventListener('click',async e=>{const th=e.target.closest('th.col');if(!th)return;
    const cur=NEED.find(l=>l&&th.classList.contains('need-'+l))||null,next=NEED[(NEED.indexOf(cur)+1)%NEED.length];
    const t=th.closest('table'),i=th.cellIndex;
    const r=await fetch('/api/need',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({code:t.dataset.code,column:th.dataset.label,level:next})});
    const j=await r.json();if(j.ok){[th,...[...t.querySelectorAll('tbody tr')].map(tr=>tr.cells[i])].forEach(c=>{c.classList.remove('need-future','need-now');if(next)c.classList.add('need-'+next)})}else{document.getElementById('msg').textContent='保存できませんでした: '+j.message}});
  let drag=null;
  const ok=(td)=>drag&&td.parentElement===drag.tr&&(td.dataset.col==='退団予定'||(drag.kind==='投手'?PIT:FLD).includes(td.dataset.col)&&td.dataset.throws===drag.throws);
  document.addEventListener('dragstart',e=>{const d=e.target.closest('.p');if(!d)return;drag={id:d.dataset.id,kind:d.dataset.kind,throws:d.dataset.throws,tr:d.closest('tr'),code:d.closest('table').dataset.code,el:d};d.classList.add('dragging');e.dataTransfer.setData('text/plain',d.dataset.id)});
  document.addEventListener('dragend',()=>{if(drag)drag.el.classList.remove('dragging');drag=null;document.querySelectorAll('td.ok').forEach(t=>t.classList.remove('ok'))});
  document.addEventListener('dragover',e=>{const td=e.target.closest('td');if(td&&ok(td)){e.preventDefault();td.classList.add('ok')}});
  document.addEventListener('dragleave',e=>{const td=e.target.closest('td');if(td)td.classList.remove('ok')});
  document.addEventListener('drop',async e=>{const td=e.target.closest('td');if(!td||!ok(td))return;e.preventDefault();
    const r=await fetch('/api/move',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({code:drag.code,player_id:drag.id,to:td.dataset.col})});
    const j=await r.json();if(j.ok){location.reload()}else{document.getElementById('msg').textContent='保存できませんでした: '+j.message}});
}
</script></body></html>
"""


def build_page(editable=False):
    names, sections = [], []
    labels = set(load_json(LABELS_PATH, []))
    overrides = load_json(OVERRIDES_PATH, {})
    leaving = set(load_json(LEAVING_PATH, []))
    all_needs = load_json(NEEDS_PATH, {})
    with npb_db.connect() as conn:
        for code in TEAM_ORDER:
            players = load_view_players(conn, code, overrides, leaving)
            names.append(TEAMS[code])
            sections.append(f"<section>{build_team(code, players, editable, labels, all_needs.get(code, {}))}</section>")
    note = ("ドラッグで列を移動できます（同じ年齢の行の中だけ。投手=先発↔中継ぎ・抑え（右・左は固定）、野手=守備位置どうし）。"
            "退団予定の列へも動かせます（保存先: leaving.json）。保存先: overrides.json（position / role）" if editable else
            "編集は <code>python3 src/build_view.py --serve</code>")
    return (PAGE.replace("__SECTIONS__", "\n".join(sections))
            .replace("__NAMES__", json.dumps(names, ensure_ascii=False))
            .replace("__DATE__", date.today().isoformat())
            .replace("__GS__", str(derive.GS_MIN_STARTER)).replace("__G__", str(derive.G_MIN_RELIEVER))
            .replace("__PA__", str(derive.PA_MIN_FIELDER))
            .replace("__EDITNOTE__", note).replace("__EDIT__", "true" if editable else "false")), len(names)


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", f"{ctype}; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.split("#")[0] in ("/", "/index.html"):
            self._send(200, build_page(editable=True)[0], "text/html")
        else:
            self._send(404, "not found", "text/plain")

    def do_POST(self):
        if self.path not in ("/api/move", "/api/label", "/api/need"):
            return self._send(404, "not found", "text/plain")
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            if self.path == "/api/label":
                ok, msg = set_label(body["player_id"], bool(body["on"]))
            elif self.path == "/api/need":
                ok, msg = set_need(body["code"], body["column"], body["level"])
            else:
                ok, msg = apply_move(body["code"], body["player_id"], body["to"])
        except (ValueError, KeyError) as e:
            ok, msg = False, f"bad request: {e}"
        self._send(200 if ok else 400, json.dumps({"ok": ok, "message": msg}, ensure_ascii=False), "application/json")

    def log_message(self, *args):
        pass


def main():
    if "--serve" in sys.argv:
        server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
        print(f"Serving on http://127.0.0.1:{PORT}  (Ctrl+C to stop)")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        return
    page, n = build_page(editable=False)
    OUT_PATH.write_text(page, encoding="utf-8")
    print(f"Wrote {OUT_PATH} ({n} teams)")


if __name__ == "__main__":
    main()
