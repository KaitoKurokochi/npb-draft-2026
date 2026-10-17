"""Values derived from npb_scraper's database (npb.sqlite): age, pitcher role, fielding position,
the regular flag and display lines. Pure functions — no I/O."""

from datetime import date

AGE_REF_DATE = date(2027, 3, 26)  # 2027 opening day (provisional; update when the schedule is out)

# "Regular" thresholds (bright text in the chart)
GS_MIN_STARTER = 20   # 先発: starts at or above this
G_MIN_RELIEVER = 40   # 中継ぎ・抑え: appearances at or above this
PA_MIN_FIELDER = 250  # 野手: 1軍 plate appearances at or above this
SV_MIN_CLOSER = 5     # saves needed to be labelled 抑え (non-starters)

ALLOWED_POSITIONS = {
    "捕手": ("捕手",),
    "内野手": ("一塁手", "二塁手", "三塁手", "遊撃手"),
    "外野手": ("外野手",),
}
FIELDER_POSITIONS = ("捕手", "一塁手", "二塁手", "三塁手", "遊撃手", "外野手")


def age_on(birth_iso, ref: date = AGE_REF_DATE):
    if not birth_iso:
        return None
    b = date.fromisoformat(birth_iso)
    return ref.year - b.year - ((ref.month, ref.day) < (b.month, b.day))


def innings(outs: int) -> str:
    """Outs as written in baseball: 160 -> '53.1'."""
    return f"{outs // 3}.{outs % 3}"


def sum_columns(rows: list[dict], *cols: str) -> dict:
    """Column sums over rows; NULLs count as 0, but a column that is NULL in every row stays None."""
    out = {}
    for c in cols:
        vals = [r[c] for r in rows if r.get(c) is not None]
        out[c] = sum(vals) if vals else None
    return out


def pitching_line(rows: list[dict]):
    """One season's pitching summed over a player's rows (several if traded); None without rows."""
    if not rows:
        return None
    t = sum_columns(rows, "g", "gs", "w", "l", "sv", "hold", "outs", "er")
    t["era"] = t["er"] * 27 / t["outs"] if t["outs"] else None
    return t


def batting_line(rows: list[dict]):
    if not rows:
        return None
    t = sum_columns(rows, "g", "pa", "ab", "h", "hr", "rbi", "sb", "bb", "hbp", "sf", "tb")
    ab, bb, hbp, sf = (t[k] or 0 for k in ("ab", "bb", "hbp", "sf"))
    t["avg"] = t["h"] / ab if ab else None
    obp_den = ab + bb + hbp + sf
    t["ops"] = ((t["h"] + bb + hbp) / obp_den + t["tb"] / ab) if ab and obp_den else None
    return t


def pitcher_role(line) -> str:
    """先発 / 抑え / 中継ぎ from this season's line; 不明 without appearances or without a start count."""
    if not line or not line["g"] or line["gs"] is None:
        return "不明"
    if line["gs"] / line["g"] >= 0.5:
        return "先発"
    return "抑え" if (line["sv"] or 0) >= SV_MIN_CLOSER else "中継ぎ"


def fielder_position(category: str, games_by_position: dict[str, int]) -> tuple[str, str]:
    """(position, 'positions' text). The position is the most-played one within the player's broad
    category; an infielder with no fielding record is 内野手(不明)."""
    allowed = ALLOWED_POSITIONS.get(category, ())
    played = [(p, games_by_position.get(p, 0)) for p in allowed if games_by_position.get(p, 0) > 0]
    if played:
        best = max(played, key=lambda pg: pg[1])[0]
    else:
        best = category if category in ("捕手", "外野手") else "内野手(不明)"
    all_played = sorted(((p, g) for p, g in games_by_position.items() if p in FIELDER_POSITIONS and g > 0),
                        key=lambda pg: -pg[1])
    return best, "/".join(f"{p}{g}" for p, g in all_played)


def is_regular(kind: str, auto_role: str, g, gs, pa) -> bool:
    if kind == "野手":
        return (pa or 0) >= PA_MIN_FIELDER
    if auto_role == "先発":
        return (gs or 0) >= GS_MIN_STARTER
    return (g or 0) >= G_MIN_RELIEVER


def pitching_text(line) -> str:
    if line is None:
        return "1軍登板なし"
    era = f"防{line['era']:.2f}" if line["era"] is not None else "防-"
    gs = "-" if line["gs"] is None else line["gs"]
    return (f"{line['g']}登板 先発{gs} {line['w'] or 0}勝{line['l'] or 0}敗 S{line['sv'] or 0} H{line['hold'] or 0} "
            f"{innings(line['outs'] or 0)}回 {era}")


def batting_text(line) -> str:
    if line is None or not line["pa"]:
        return "1軍打席なし"
    avg = f".{round(line['avg'] * 1000):03d}" if line["avg"] is not None else "-"
    ops = f".{round(line['ops'] * 1000):03d}" if line["ops"] is not None else "-"
    return f"{line['g']}試合 {line['pa']}打席 打率{avg} {line['hr']}本 {line['rbi']}点 {line['sb']}盗 OPS{ops}"
