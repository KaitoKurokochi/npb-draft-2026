"""
roster_analyzer.py
------------------
Phase 1: NPB 2026 roster analysis using batting stats from NPB-scraper.

Inputs:
  data/rosters/batters_2026_all.csv  -- from NPB-scraper (2026 season, ~40 games)
  data/rosters/player_positions.csv  -- manually maintained (position + age)
                                        generated as template if missing

Outputs:
  results/team_depth_summary.csv     -- A/B/C/X rank counts per team per position
  results/player_ranks.csv           -- all position players with computed rank
  data/rosters/player_positions_template.csv  -- template for manual position entry
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pandas as pd
import numpy as np

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
BATTERS_CSV = ROOT / "data" / "rosters" / "batters_2026_all.csv"
POSITIONS_CSV = ROOT / "data" / "rosters" / "player_positions.csv"
TEMPLATE_CSV = ROOT / "data" / "rosters" / "player_positions_template.csv"
RESULTS_DIR = ROOT / "results"

RESULTS_DIR.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# Team metadata
# ---------------------------------------------------------------------------
TEAM_META = {
    "g":  {"name_jp": "読売ジャイアンツ",           "league": "central"},
    "t":  {"name_jp": "阪神タイガース",             "league": "central"},
    "db": {"name_jp": "横浜DeNAベイスターズ",       "league": "central"},
    "c":  {"name_jp": "広島東洋カープ",             "league": "central"},
    "d":  {"name_jp": "中日ドラゴンズ",             "league": "central"},
    "s":  {"name_jp": "東京ヤクルトスワローズ",     "league": "central"},
    "h":  {"name_jp": "福岡ソフトバンクホークス",   "league": "pacific"},
    "l":  {"name_jp": "埼玉西武ライオンズ",         "league": "pacific"},
    "e":  {"name_jp": "東北楽天ゴールデンイーグルス", "league": "pacific"},
    "m":  {"name_jp": "千葉ロッテマリーンズ",       "league": "pacific"},
    "f":  {"name_jp": "北海道日本ハムファイターズ", "league": "pacific"},
    "b":  {"name_jp": "オリックス・バファローズ",   "league": "pacific"},
}

# ---------------------------------------------------------------------------
# Position groups
# ---------------------------------------------------------------------------
# Used for position-level depth analysis (once player_positions.csv is filled)
POSITION_GROUPS = {
    "battery": ["C", "SP", "RP"],
    "infield":  ["1B", "2B", "3B", "SS"],
    "outfield": ["LF", "CF", "RF", "DH"],
    "utility":  ["UT"],
}

# All valid position codes
VALID_POSITIONS = ["C", "1B", "2B", "3B", "SS", "LF", "CF", "RF", "DH", "UT", "SP", "RP"]

# ---------------------------------------------------------------------------
# Ranking thresholds
# (calibrated for ~40-game point in season; max PA ≈ 190)
# ---------------------------------------------------------------------------
# Rank A: clear starter, quality production
RANK_A_PA  = 100
RANK_A_OPS = 0.750

# Rank B: regular or solid backup
RANK_B_PA  = 55
RANK_B_OPS = 0.620

# Rank C: bench / part-time / developing
RANK_C_PA  = 15

# Rank X: veteran with very limited playing time (upcoming roster hole)
RANK_X_G   = 20   # appeared in many games but...
RANK_X_PA  = 40   # ...got few plate appearances

# Players with PA < PA_PITCHER_CUTOFF are treated as pitchers or inactive
PA_PITCHER_CUTOFF = 5


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

def load_batters() -> pd.DataFrame:
    """Load and enrich the NPB-scraper batting CSV."""
    df = pd.read_csv(BATTERS_CSV)
    df["OPS"] = df["OBP"] + df["SLG"]
    df["team_jp"] = df["team_code"].map(lambda c: TEAM_META.get(c, {}).get("name_jp", c))
    return df


def assign_rank(row: pd.Series) -> str:
    """Assign A/B/C/X rank based on PA and OPS.

    Returns:
        "A"  — clear starter with quality production
        "B"  — regular or solid backup
        "C"  — bench / part-time / young prospect
        "X"  — veteran with declining playing time (future hole)
        "-"  — pitcher or essentially inactive (PA < cutoff)
    """
    pa  = row["PA"]  if pd.notna(row["PA"])  else 0
    ops = row["OPS"] if pd.notna(row["OPS"]) else 0.0
    g   = row["G"]   if pd.notna(row["G"])   else 0

    if pa < PA_PITCHER_CUTOFF:
        return "-"  # likely pitcher or inactive
    if pa >= RANK_A_PA and ops >= RANK_A_OPS:
        return "A"
    if pa >= RANK_B_PA and ops >= RANK_B_OPS:
        return "B"
    if g >= RANK_X_G and pa < RANK_X_PA:
        return "X"
    if pa >= RANK_C_PA:
        return "C"
    return "-"


def compute_ranks(df: pd.DataFrame) -> pd.DataFrame:
    """Add 'rank' column to the dataframe."""
    df = df.copy()
    df["rank"] = df.apply(assign_rank, axis=1)
    return df


def build_team_depth_summary(df: pd.DataFrame) -> pd.DataFrame:
    """Count A/B/C/X players per team (position players only, '-' excluded)."""
    pos_players = df[df["rank"] != "-"].copy()
    summary = (
        pos_players
        .groupby(["team_code", "team_jp", "rank"])
        .size()
        .unstack(fill_value=0)
        .reset_index()
    )
    # Ensure all rank columns exist
    for r in ["A", "B", "C", "X"]:
        if r not in summary.columns:
            summary[r] = 0
    summary["total"] = summary[["A", "B", "C", "X"]].sum(axis=1)
    summary["depth_score"] = summary["A"] * 3 + summary["B"] * 2 + summary["C"] * 1
    summary = summary.sort_values("depth_score", ascending=False)
    return summary[["team_code", "team_jp", "A", "B", "C", "X", "total", "depth_score"]]


def generate_positions_template(df: pd.DataFrame) -> pd.DataFrame:
    """Create a template CSV for manual position/age entry.

    Includes all position players (rank != '-'), sorted by team then PA desc.
    """
    pos_players = df[df["rank"] != "-"].copy()
    template = pos_players[["team_code", "team_jp", "player_name", "bats", "G", "PA", "OPS", "rank"]].copy()
    template = template.sort_values(["team_code", "PA"], ascending=[True, False])

    # Columns to fill in manually
    template["position"] = ""   # C/1B/2B/3B/SS/LF/CF/RF/DH/UT/SP/RP
    template["age_2026"] = ""   # age as of 2026 season
    template["note"] = ""       # free text (FA status, injury, etc.)

    return template


def load_positions() -> pd.DataFrame | None:
    """Load manually-filled positions CSV, or return None if not present."""
    if not POSITIONS_CSV.exists():
        return None
    pos = pd.read_csv(POSITIONS_CSV)
    # Validate position codes
    invalid = pos[~pos["position"].isin(VALID_POSITIONS + [""])]
    if not invalid.empty:
        print(f"[WARN] Unknown position codes found:\n{invalid[['player_name','position']]}")
    return pos


def build_position_depth(df_ranks: pd.DataFrame, df_pos: pd.DataFrame) -> pd.DataFrame:
    """Merge rank data with position assignments and build per-position depth table.

    Returns a DataFrame with rows = (team × position) and columns = A/B/C/X counts.
    """
    merged = df_ranks.merge(
        df_pos[["player_name", "team_code", "position", "age_2026"]],
        on=["player_name", "team_code"],
        how="left",
    )
    merged = merged[merged["position"].notna() & (merged["position"] != "")]

    depth = (
        merged
        .groupby(["team_code", "team_jp", "position", "rank"])
        .size()
        .unstack(fill_value=0)
        .reset_index()
    )
    for r in ["A", "B", "C", "X"]:
        if r not in depth.columns:
            depth[r] = 0

    # Flag positions with zero A+B ranked players as "THIN"
    depth["is_thin"] = (depth["A"] + depth["B"]) == 0
    return depth


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("=" * 60)
    print("NPB 2026 Roster Analyzer — Phase 1")
    print("=" * 60)

    # 1. Load data
    df = load_batters()
    print(f"\nLoaded {len(df)} players from {BATTERS_CSV.name}")

    # 2. Compute ranks
    df = compute_ranks(df)
    rank_counts = df["rank"].value_counts()
    print(f"\nRank distribution (all players):")
    for r in ["A", "B", "C", "X", "-"]:
        print(f"  {r}: {rank_counts.get(r, 0)}")

    # 3. Team depth summary
    summary = build_team_depth_summary(df)
    print("\n--- Team Depth Summary (position players) ---")
    print(summary.to_string(index=False))
    out_summary = RESULTS_DIR / "team_depth_summary.csv"
    summary.to_csv(out_summary, index=False, encoding="utf-8-sig")
    print(f"\nSaved -> {out_summary}")

    # 4. Save player ranks
    out_ranks = RESULTS_DIR / "player_ranks.csv"
    rank_cols = ["team_code", "team_jp", "player_name", "bats", "G", "PA", "OPS", "rank"]
    df[rank_cols].sort_values(["team_code", "PA"], ascending=[True, False]).to_csv(
        out_ranks, index=False, encoding="utf-8-sig"
    )
    print(f"Saved -> {out_ranks}")

    # 5. Generate positions template (if not already filled)
    if not POSITIONS_CSV.exists():
        template = generate_positions_template(df)
        template.to_csv(TEMPLATE_CSV, index=False, encoding="utf-8-sig")
        print(f"\n[ACTION REQUIRED] Positions template saved -> {TEMPLATE_CSV}")
        print("  Fill in 'position' and 'age_2026' columns, then copy to:")
        print(f"  {POSITIONS_CSV}")
    else:
        print(f"\nPositions file found: {POSITIONS_CSV}")
        df_pos = load_positions()
        if df_pos is not None:
            depth = build_position_depth(df, df_pos)
            out_depth = RESULTS_DIR / "position_depth.csv"
            depth.to_csv(out_depth, index=False, encoding="utf-8-sig")
            print(f"Position depth saved -> {out_depth}")

            # Print thin positions
            thin = depth[depth["is_thin"]]
            if not thin.empty:
                print("\n--- THIN positions (no A or B ranked player) ---")
                print(thin[["team_jp", "position", "A", "B", "C", "X"]].to_string(index=False))

    print("\nDone.")


if __name__ == "__main__":
    main()
