"""
roster_analyzer.py
------------------
Phase 1: NPB 2026 roster analysis using batting + fielding stats from NPB-scraper.

Inputs:
  data/rosters/batters_2026_all.csv   -- 1軍 batting stats (NPB-scraper idb1)
  data/rosters/fielding_2026_all.csv  -- 1軍 fielding stats (NPB-scraper idf1)
  data/rosters/farm_batters_2026_all.csv  -- 2軍 batting stats (idb2)
  data/rosters/farm_fielding_2026_all.csv -- 2軍 fielding stats (idf2)

Outputs:
  results/batters_with_positions.csv  -- batters enriched with primary_position/positions
  results/team_depth_summary.csv      -- A/B/C/X rank counts per team
  results/position_depth.csv          -- depth per team per position (A/B/C/X)
  results/player_ranks.csv            -- all position players with computed rank
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data" / "rosters"
RESULTS_DIR = ROOT / "results"
RESULTS_DIR.mkdir(exist_ok=True)

BATTERS_CSV      = DATA_DIR / "batters_2026_all.csv"
FIELDING_CSV     = DATA_DIR / "fielding_2026_all.csv"
FARM_BATTERS_CSV = DATA_DIR / "farm_batters_2026_all.csv"
FARM_FIELDING_CSV= DATA_DIR / "farm_fielding_2026_all.csv"

# ---------------------------------------------------------------------------
# Team metadata
# ---------------------------------------------------------------------------
TEAM_META = {
    "g":  {"name_jp": "読売ジャイアンツ",             "league": "central"},
    "t":  {"name_jp": "阪神タイガース",               "league": "central"},
    "db": {"name_jp": "横浜DeNAベイスターズ",         "league": "central"},
    "c":  {"name_jp": "広島東洋カープ",               "league": "central"},
    "d":  {"name_jp": "中日ドラゴンズ",               "league": "central"},
    "s":  {"name_jp": "東京ヤクルトスワローズ",       "league": "central"},
    "h":  {"name_jp": "福岡ソフトバンクホークス",     "league": "pacific"},
    "l":  {"name_jp": "埼玉西武ライオンズ",           "league": "pacific"},
    "e":  {"name_jp": "東北楽天ゴールデンイーグルス", "league": "pacific"},
    "m":  {"name_jp": "千葉ロッテマリーンズ",         "league": "pacific"},
    "f":  {"name_jp": "北海道日本ハムファイターズ",   "league": "pacific"},
    "b":  {"name_jp": "オリックス・バファローズ",     "league": "pacific"},
}

# ---------------------------------------------------------------------------
# Ranking thresholds  (calibrated for ~130-game point, max PA ≈ 450)
# ---------------------------------------------------------------------------
RANK_A_PA  = 280   # clear starter (~3 PA/game × 90+ games)
RANK_A_OPS = 0.720

RANK_B_PA  = 150   # regular or solid backup
RANK_B_OPS = 0.620

RANK_C_PA  = 40    # bench / part-time / developing

RANK_X_G   = 50    # appeared in many games but...
RANK_X_PA  = 120   # ...got few plate appearances (declining veteran)

PA_PITCHER_CUTOFF = 10   # below this → pitcher / inactive


# ---------------------------------------------------------------------------
# Position aggregation
# ---------------------------------------------------------------------------

def merge_positions(batters: pd.DataFrame, fielding: pd.DataFrame) -> pd.DataFrame:
    """Add primary_position and positions columns to the batters DataFrame.

    primary_position: position where the player appeared in the most games
    positions:        all positions played, comma-separated, sorted by G desc
                      e.g. "SS,2B,3B"

    Players with no fielding record get primary_position="" and positions="".
    """
    def _agg(grp: pd.DataFrame) -> pd.Series:
        grp_sorted = grp.sort_values("G", ascending=False)
        primary = grp_sorted.iloc[0]["position"]
        all_pos = ",".join(grp_sorted["position"].tolist())
        return pd.Series({"primary_position": primary, "positions": all_pos})

    pos_agg = (
        fielding.groupby(["team_code", "player_name"])
        .apply(_agg, include_groups=False)
        .reset_index()
    )

    merged = batters.merge(pos_agg, on=["team_code", "player_name"], how="left")
    merged["primary_position"] = merged["primary_position"].fillna("")
    merged["positions"] = merged["positions"].fillna("")
    return merged


# ---------------------------------------------------------------------------
# Quality ranking
# ---------------------------------------------------------------------------

def assign_rank(row: pd.Series) -> str:
    """Assign A/B/C/X/- rank based on PA and OPS.

    A  — clear starter with quality production
    B  — regular or solid backup
    C  — bench / part-time / young prospect
    X  — veteran with declining playing time (future roster hole)
    -  — pitcher / inactive (PA below cutoff)
    """
    pa  = row["PA"]  if pd.notna(row["PA"])  else 0
    ops = row["OPS"] if pd.notna(row["OPS"]) else 0.0
    g   = row["G"]   if pd.notna(row["G"])   else 0

    if pa < PA_PITCHER_CUTOFF:
        return "-"
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
    df = df.copy()
    df["OPS"] = df["OBP"] + df["SLG"]
    df["team_jp"] = df["team_code"].map(lambda c: TEAM_META.get(c, {}).get("name_jp", c))
    df["rank"] = df.apply(assign_rank, axis=1)
    return df


# ---------------------------------------------------------------------------
# Summary tables
# ---------------------------------------------------------------------------

def build_team_depth_summary(df: pd.DataFrame) -> pd.DataFrame:
    pos_players = df[df["rank"] != "-"].copy()
    summary = (
        pos_players
        .groupby(["team_code", "team_jp", "rank"])
        .size()
        .unstack(fill_value=0)
        .reset_index()
    )
    for r in ["A", "B", "C", "X"]:
        if r not in summary.columns:
            summary[r] = 0
    summary["total"] = summary[["A", "B", "C", "X"]].sum(axis=1)
    summary["depth_score"] = summary["A"] * 3 + summary["B"] * 2 + summary["C"]
    return summary.sort_values("depth_score", ascending=False)[
        ["team_code", "team_jp", "A", "B", "C", "X", "total", "depth_score"]
    ]


def build_position_depth(df: pd.DataFrame) -> pd.DataFrame:
    """Depth table per (team × primary_position), for position players only."""
    pos_players = df[(df["rank"] != "-") & (df["primary_position"] != "")].copy()
    depth = (
        pos_players
        .groupby(["team_code", "team_jp", "primary_position", "rank"])
        .size()
        .unstack(fill_value=0)
        .reset_index()
    )
    for r in ["A", "B", "C", "X"]:
        if r not in depth.columns:
            depth[r] = 0
    depth["is_thin"] = (depth["A"] + depth["B"]) == 0
    return depth.sort_values(["team_code", "primary_position"])


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("=" * 60)
    print("NPB 2026 Roster Analyzer — Phase 1")
    print("=" * 60)

    # 1. Load data
    batters = pd.read_csv(BATTERS_CSV)
    fielding = pd.read_csv(FIELDING_CSV)
    print(f"\nLoaded {len(batters)} batters (1軍), {len(fielding)} fielding rows (1軍)")

    # 2. Merge positions into batters
    df = merge_positions(batters, fielding)
    df = compute_ranks(df)

    # How many got position data?
    matched = (df["primary_position"] != "").sum()
    print(f"Matched position data: {matched}/{len(df)} players")

    # 3. Save enriched batters CSV
    out_batters = RESULTS_DIR / "batters_with_positions.csv"
    rank_cols = [
        "team_code", "team_jp", "player_name", "bats",
        "G", "PA", "OPS", "rank",
        "primary_position", "positions",
    ]
    df[rank_cols].sort_values(["team_code", "PA"], ascending=[True, False]).to_csv(
        out_batters, index=False, encoding="utf-8-sig"
    )
    print(f"Saved -> {out_batters}")

    # 4. Rank distribution
    rank_counts = df["rank"].value_counts()
    print("\nRank distribution:")
    for r in ["A", "B", "C", "X", "-"]:
        print(f"  {r}: {rank_counts.get(r, 0)}")

    # 5. Team depth summary
    summary = build_team_depth_summary(df)
    print("\n--- Team Depth Summary ---")
    print(summary.to_string(index=False))
    summary.to_csv(RESULTS_DIR / "team_depth_summary.csv", index=False, encoding="utf-8-sig")

    # 6. Position depth (requires position data)
    pos_depth = build_position_depth(df)
    pos_depth.to_csv(RESULTS_DIR / "position_depth.csv", index=False, encoding="utf-8-sig")
    print(f"\nSaved position depth -> {RESULTS_DIR / 'position_depth.csv'}")

    # 7. Thin positions
    thin = pos_depth[pos_depth["is_thin"]]
    if not thin.empty:
        print("\n--- THIN positions (no A or B ranked player at primary position) ---")
        print(thin[["team_jp", "primary_position", "A", "B", "C", "X"]].to_string(index=False))

    # 8. Save full player_ranks
    df[rank_cols].to_csv(RESULTS_DIR / "player_ranks.csv", index=False, encoding="utf-8-sig")

    print("\nDone.")


if __name__ == "__main__":
    main()
