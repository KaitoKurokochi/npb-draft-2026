"""
Build a browser-viewable roster chart (age x position) for each of the 12 teams.

Reads : $NPB_DATA/npb-draft-2026/rosters/<code>_members.md   (written by fetch.py)
Writes: $NPB_DATA/npb-draft-2026/roster_view.html

Rows   : age on the 2027 opening day (~19, 20, ..., 34, 35~)
Columns: 先発 / 中継ぎ・抑え / 捕手 / 一塁手 / 二塁手 / 三塁手 / 遊撃手 / 外野手 (+ 内野手(不明) if any)
Cells  : '<surname>#<number>' (hover for details); bright = regular (see thresholds above), dim = others. Pitchers sorted by IP, fielders by PA.

Moving players (planned future position):
  Dragging a player in --serve mode rewrites that player's `position` (fielders) or `role`
  (pitchers) in <code>_members.md directly. The `auto` column keeps the automatic judgement, so
  a value that differs from `auto` is a manual edit (fetch.py carries such edits over).
  A player can only be moved to another column of the same kind (pitchers: 先発/中継ぎ・抑え,
  fielders: 捕手/一塁手/二塁手/三塁手/遊撃手/外野手) within the same age row.

Usage:
  python src/build_view.py            # write a static roster_view.html (not editable)
  python src/build_view.py --serve    # editable page at http://127.0.0.1:8765 (drag to move players)
"""

import html
import json
import os
import re
import sys
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NPB_DATA = Path(os.environ.get("NPB_DATA", ROOT / "data"))
DATA_ROOT = NPB_DATA / "npb-draft-2026" if "NPB_DATA" in os.environ else NPB_DATA
ROSTER_DIR = DATA_ROOT / "rosters"
OUT_PATH = DATA_ROOT / "roster_view.html"
PORT = 8765

# Binary "regular" flag -> bright text; everything else is dimmed.
GS_MIN_STARTER = 20   # 先発: 先発数 (GS) at or above this
G_MIN_RELIEVER = 40   # 中継ぎ・抑え: 登板数 (G) at or above this
PA_MIN_FIELDER = 250  # 野手: 1軍の打席数 (PA) at or above this

TEAM_ORDER = ["g", "t", "db", "c", "d", "s", "h", "l", "e", "m", "f", "b"]
AGE_ROWS = ["~19"] + [str(a) for a in range(20, 35)] + ["35~"]

# column key -> label
COLUMNS = [
    ("先発", "先発"),
    ("救援", "中継ぎ・抑え"),
    ("捕手", "捕手"),
    ("一塁手", "一塁手"),
    ("二塁手", "二塁手"),
    ("三塁手", "三塁手"),
    ("遊撃手", "遊撃手"),
    ("内野手(不明)", "内野手(不明)"),
    ("外野手", "外野手"),
]


def parse_members(path):
    """Return (team name, header lines, players) from a team file written by fetch.py."""
    text = path.read_text(encoding="utf-8")
    m = re.match(r"# (.+?) 支配下選手一覧", text)
    team = m.group(1) if m else path.stem
    players, cols, kind = [], None, None
    for line in text.split("\n"):
        if line.startswith("## "):
            kind = line[3:].strip()
            cols = None
        elif line.startswith("|") and kind in ("投手", "野手"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if cols is None:
                cols = cells
            elif not set(cells[0]) <= {"-"}:
                players.append({"kind": kind, **dict(zip(cols, cells))})
    return team, players


def column_of(p):
    if p["kind"] == "投手":
        return "先発" if p.get("role") == "先発" else "救援"  # 中継ぎ / 抑え / 不明 -> 救援
    return p.get("position", "")


def age_row(p):
    return age_label(p.get("age", ""))


def age_label(age):
    if not age.isdigit():
        return "年齢不明"
    a = int(age)
    return "~19" if a <= 19 else "35~" if a >= 35 else str(a)


def sort_key(p):
    num = p.get("IP") if p["kind"] == "投手" else p.get("PA")
    try:
        return -float(num)
    except (TypeError, ValueError):
        return 0.0


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def is_regular(p):
    if p["kind"] == "野手":
        return num(p.get("PA")) >= PA_MIN_FIELDER
    if (p.get("auto") or p.get("role")) == "先発":
        return num(p.get("GS")) >= GS_MIN_STARTER
    return num(p.get("G")) >= G_MIN_RELIEVER


def label(p):
    """'<surname>#<number>'."""
    return f"{html.escape(p['name'].split()[0])}#{html.escape(p['number'])}"


def tooltip(p):
    parts = [f"#{p['number']}", p.get("birth", ""), f"{p.get('age', '?')}歳"]
    if p["kind"] == "投手":
        parts += [f"{p.get('throws', '')}投{p.get('bats', '')}打", f"role={p.get('role', '')}",
                  f"G{p.get('G', '')}/GS{p.get('GS', '')}/SV{p.get('SV', '')}/HLD{p.get('HLD', '')}/IP{p.get('IP', '')}"]
    else:
        parts += [f"{p.get('throws', '')}投{p.get('bats', '')}打", p.get("positions", ""),
                  f"PA{p.get('PA', '')} OPS{p.get('OPS', '')}" if p.get("PA") else "1軍打席なし"]
    parts += [p.get("draft", "") or "ドラフト外", p.get("career", "")]
    return " / ".join(x for x in parts if x)


PIT_COLS = ("先発", "救援")
FLD_COLS = ("捕手", "一塁手", "二塁手", "三塁手", "遊撃手", "外野手")


def allowed_targets(kind):
    return PIT_COLS if kind == "投手" else FLD_COLS


def auto_column(p):
    """Column of the automatic judgement (`auto`); falls back to the current value."""
    if p["kind"] == "投手":
        return "先発" if (p.get("auto") or p.get("role")) == "先発" else "救援"
    return p.get("auto") or p.get("position", "")


def apply_move(code, pid, to):
    """Rewrite `position` / `role` of one player in <code>_members.md; return (ok, message)."""
    if code not in TEAM_ORDER:
        return False, "unknown team"
    path = ROSTER_DIR / f"{code}_members.md"
    lines = path.read_text(encoding="utf-8").split("\n")
    kind, cols, done = None, None, False
    for i, line in enumerate(lines):
        if line.startswith("## "):
            kind, cols = line[3:].strip(), None
        elif line.startswith("|") and kind in ("投手", "野手"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if cols is None:
                cols = cells
                continue
            d = dict(zip(cols, cells))
            if d.get("player_id") != pid:
                continue
            if to not in allowed_targets(kind):
                return False, f"{to} is not allowed for {kind}"
            field = "role" if kind == "投手" else "position"
            if "auto" not in cols:
                return False, "no auto column: re-run fetch.py or the migration first"
            auto = d["auto"]
            if kind == "投手":
                # moving back to the auto column restores `auto`; otherwise 先発 or 中継ぎ
                new = auto if auto_column({"kind": kind, "auto": auto}) == to else ("先発" if to == "先発" else "中継ぎ")
            else:
                new = to
            cells[cols.index(field)] = new
            lines[i] = "| " + " | ".join(cells) + " |"
            done = True
            break
    if not done:
        return False, "unknown player"
    tmp = path.with_suffix(".tmp")
    tmp.write_text("\n".join(lines), encoding="utf-8")
    tmp.replace(path)
    return True, "ok"


def player_div(code, p, editable):
    orig, now = auto_column(p), column_of(p)
    moved = orig != now
    tip = tooltip(p) + (f" / 手動で移動: {orig} → {now}" if moved else "")
    cls = f"p {'hi' if is_regular(p) else 'lo'}{' moved' if moved else ''}"
    drag = " draggable='true'" if editable else ""
    return (f"<div class='{cls}'{drag} data-id='{html.escape(p.get('player_id', ''))}' data-kind='{p['kind']}' "
            f"title='{html.escape(tip, quote=True)}'>{label(p)}</div>")


def build_team(code, editable):
    path = ROSTER_DIR / f"{code}_members.md"
    team, players = parse_members(path)
    grid, counts = {}, {}
    for p in sorted(players, key=sort_key):
        row, col = age_row(p), column_of(p)
        grid.setdefault(row, {}).setdefault(col, []).append(p)
        counts[col] = counts.get(col, 0) + 1
    rows = AGE_ROWS + (["年齢不明"] if "年齢不明" in grid else [])
    cols = [(k, lab) for k, lab in COLUMNS if k != "内野手(不明)" or counts.get(k)]

    out = [f"<h2>{html.escape(team)} <small>{len(players)}名</small></h2>", f"<div class=wrap><table data-code='{code}'>"]
    out.append("<thead><tr><th>年齢</th>" + "".join(
        f"<th>{html.escape(lab)}<span class=n>{counts.get(k, 0)}</span></th>" for k, lab in cols) + "<th>計</th></tr></thead><tbody>")
    for r in rows:
        cells, total = [], 0
        for k, _ in cols:
            ps = grid.get(r, {}).get(k, [])
            total += len(ps)
            cells.append(f"<td data-col='{k}'>" + "".join(player_div(code, p, editable) for p in ps) + "</td>")
        out.append(f"<tr class='{'empty' if total == 0 else ''}'><th>{r}</th>{''.join(cells)}<td class=tot>{total or ''}</td></tr>")
    out.append("</tbody></table></div>")
    return team, "\n".join(out)


PAGE = """<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>NPB 2026 現状戦力</title>
<style>
:root{--bg:#fff;--fg:#1c1f24;--mute:#6b7280;--line:#e2e5ea;--head:#f3f4f6;--acc:#1d4ed8;--accbg:#e8efff;--pit:var(--fg);--fld:#1c1f24}
@media (prefers-color-scheme:dark){:root{--bg:#14161a;--fg:#e6e8ec;--mute:#9aa1ad;--line:#2d3139;--head:#1d2025;--acc:#8ab4ff;--accbg:#1f2a44;--pit:var(--fg);--fld:#e6e8ec}}
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
.p{cursor:default}.p[draggable=true]{cursor:grab}td.ok{background:var(--accbg)}.p.dragging{opacity:.4}#msg{color:var(--acc);font-size:12px}.p.hi{color:var(--fg);font-weight:600}.p.lo{color:var(--mute)}
</style></head><body>
<header><h1>NPB 2026 現状戦力</h1>
<p class=note>行 = 2027年開幕時の年齢（仮: 3/26時点）／ 名字#背番号／ 明るい文字 = 先発GS≥__GS__ ・ 救援G≥__G__ ・ 野手PA≥__PA__（それ以外は暗い）／ カーソルで詳細／ 退団者（自由契約・引退）と育成選手は除く ／ 生成日 __DATE__<br>__EDITNOTE__</p><p id=msg></p></header>
<nav id=tabs></nav>
__SECTIONS__
<script>
const names=__NAMES__;const tabs=document.getElementById('tabs');const secs=[...document.querySelectorAll('section')];
function show(i){secs.forEach((s,j)=>s.classList.toggle('on',i===j));[...tabs.children].forEach((b,j)=>b.setAttribute('aria-selected',i===j));history.replaceState(null,'','#'+i)}
names.forEach((n,i)=>{const b=document.createElement('button');b.textContent=n;b.onclick=()=>show(i);tabs.appendChild(b)});
show(Math.min(parseInt(location.hash.slice(1))||0,names.length-1));
const EDIT=__EDIT__;
if(EDIT){
  const PIT=['先発','救援'],FLD=['捕手','一塁手','二塁手','三塁手','遊撃手','外野手'];
  let drag=null;
  const ok=(td)=>drag&&td.dataset.col&&(drag.kind==='投手'?PIT:FLD).includes(td.dataset.col)&&td.parentElement===drag.tr;
  document.addEventListener('dragstart',e=>{const d=e.target.closest('.p');if(!d)return;drag={id:d.dataset.id,kind:d.dataset.kind,tr:d.closest('tr'),code:d.closest('table').dataset.code,el:d};d.classList.add('dragging');e.dataTransfer.setData('text/plain',d.dataset.id)});
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
    for code in TEAM_ORDER:
        if not (ROSTER_DIR / f"{code}_members.md").exists():
            print(f"skip {code}: no file")
            continue
        team, body = build_team(code, editable)
        names.append(team)
        sections.append(f"<section>{body}</section>")
    note = ("ドラッグで列を移動できます（同じ年齢の行の中だけ。投手=先発↔中継ぎ・抑え、野手=守備位置どうし）。"
            "保存先: 各球団の _members.md（position / role を書き換え）" if editable else
            "編集は <code>python src/build_view.py --serve</code>")
    return (PAGE.replace("__SECTIONS__", "\n".join(sections))
            .replace("__NAMES__", json.dumps(names, ensure_ascii=False))
            .replace("__DATE__", date.today().isoformat())
            .replace("__GS__", str(GS_MIN_STARTER)).replace("__G__", str(G_MIN_RELIEVER))
            .replace("__PA__", str(PA_MIN_FIELDER))
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
        if self.path != "/api/move":
            return self._send(404, "not found", "text/plain")
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
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
