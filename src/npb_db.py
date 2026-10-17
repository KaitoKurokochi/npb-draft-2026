"""I/O: read-only access to npb_scraper's database ($NPB_DATA/npb_scraper/npb.sqlite)."""

import os
import sqlite3
from pathlib import Path

SEASON = 2026       # roster / current-season year
PREV_SEASON = 2025


def db_path() -> Path:
    base = os.environ.get("NPB_DATA")
    if not base:
        raise RuntimeError("NPB_DATA is not set (run `source ~/.claude/.agent_home`)")
    return Path(base) / "npb_scraper" / "npb.sqlite"


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def load_team(conn, team: str) -> list[dict]:
    """Active 支配下 players of a team for SEASON (no 育成, no 退団), each with raw stat rows:
    'pitching' / 'batting': {year: [rows]} (1軍), 'fielding': [rows] of SEASON (1軍 and ファーム)."""
    players = [dict(r) for r in conn.execute(
        """SELECT p.player_id, p.name, p.birth_date, p.throws, p.bats, p.category, r.number
           FROM roster r JOIN players p USING (player_id)
           WHERE r.year = ? AND r.team = ? AND r.is_development = 0 AND r.departure IS NULL""", (SEASON, team))]
    by_id = {p["player_id"]: p for p in players}
    for p in players:
        p["pitching"], p["batting"], p["fielding"] = {}, {}, []
    marks = ",".join("?" * len(by_id))
    for kind in ("pitching", "batting"):
        for r in conn.execute(f"SELECT * FROM {kind} WHERE level = 1 AND year IN (?, ?) AND player_id IN ({marks})",
                              (PREV_SEASON, SEASON, *by_id)):
            by_id[r["player_id"]][kind].setdefault(r["year"], []).append(dict(r))
    for r in conn.execute(f"SELECT * FROM fielding WHERE year = ? AND player_id IN ({marks})", (SEASON, *by_id)):
        by_id[r["player_id"]]["fielding"].append(dict(r))
    return players
