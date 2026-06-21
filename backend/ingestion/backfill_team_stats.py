"""Backfill rest_days and form_last5 from historical match data.

rest_days: For each team, compute days since last match based on kickoff_at.
form_last5: For each team, compute W/D/L from their last 5 finished matches.

Run once to fix the default values in the teams table.
"""
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone

DB_PATH = "./database.sqlite"


def backfill_rest_days(conn: sqlite3.Connection) -> int:
    """For each team, compute average rest_days from actual match schedule."""
    c = conn.cursor()

    # Get all finished matches ordered by kickoff time
    c.execute("""
        SELECT m.home_team_id, m.away_team_id, m.kickoff_at
        FROM matches m
        WHERE m.status = 'FINISHED' AND m.kickoff_at IS NOT NULL
        ORDER BY m.kickoff_at ASC
    """)
    rows = c.fetchall()

    # Build per-team match date list
    team_matches: dict[int, list[datetime]] = defaultdict(list)
    for home_id, away_id, kickoff_at in rows:
        if isinstance(kickoff_at, str):
            kickoff_at = kickoff_at.replace("Z", "+00:00")
            try:
                dt = datetime.fromisoformat(kickoff_at)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                continue
        elif isinstance(kickoff_at, datetime):
            dt = kickoff_at
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        else:
            continue
        team_matches[home_id].append(dt)
        team_matches[away_id].append(dt)

    # Compute median rest days for each team
    team_rest: dict[int, int] = {}
    for team_id, dates in team_matches.items():
        if len(dates) < 2:
            team_rest[team_id] = 7  # default
            continue
        dates.sort()
        gaps = []
        for i in range(1, len(dates)):
            gap = (dates[i] - dates[i-1]).days
            if 1 <= gap <= 30:  # reasonable gap range
                gaps.append(gap)
        if gaps:
            gaps.sort()
            median_gap = gaps[len(gaps) // 2]
            team_rest[team_id] = median_gap
        else:
            team_rest[team_id] = 7

    # Update teams table
    updated = 0
    for team_id, rest in team_rest.items():
        c.execute("UPDATE teams SET rest_days = ? WHERE id = ? AND (rest_days = 7 OR rest_days IS NULL OR rest_days > 30)", (rest, team_id))
        if c.rowcount > 0:
            updated += c.rowcount

    conn.commit()
    return updated


def backfill_form_last5(conn: sqlite3.Connection) -> int:
    """For each team, compute W/D/L string from last 5 finished matches."""
    c = conn.cursor()

    # Get all finished matches with outcomes
    c.execute("""
        SELECT m.id, m.home_team_id, m.away_team_id, m.actual_outcome, m.kickoff_at
        FROM matches m
        WHERE m.status = 'FINISHED' AND m.actual_outcome IS NOT NULL
        ORDER BY m.kickoff_at DESC
    """)
    rows = c.fetchall()

    # Build per-team recent results
    team_results: dict[int, list[str]] = defaultdict(list)
    for _match_id, home_id, away_id, outcome, kickoff_at in rows:
        if outcome == "home":
            team_results[home_id].append("W")
            team_results[away_id].append("L")
        elif outcome == "draw":
            team_results[home_id].append("D")
            team_results[away_id].append("D")
        elif outcome == "away":
            team_results[home_id].append("L")
            team_results[away_id].append("W")

    # Update teams with their form string (last 5)
    updated = 0
    for team_id, results in team_results.items():
        form = "".join(results[:5])  # most recent 5 (already DESC order)
        if not form:
            continue
        c.execute("UPDATE teams SET form_last5 = ?, form_factor = ? WHERE id = ?",
                   (form, _compute_form_factor(form), team_id))
        if c.rowcount > 0:
            updated += c.rowcount

    conn.commit()
    return updated


def _compute_form_factor(form: str) -> float:
    """Convert form string to a factor: W=+0.1, D=0, L=-0.1, centered at 1.0."""
    if not form:
        return 1.0
    score = sum(0.1 if c == "W" else (-0.1 if c == "L" else 0.0) for c in form)
    return max(0.5, min(1.5, 1.0 + score))


if __name__ == "__main__":
    conn = sqlite3.connect(DB_PATH)
    try:
        rest_updated = backfill_rest_days(conn)
        form_updated = backfill_form_last5(conn)
        print(f"rest_days updated: {rest_updated} teams")
        print(f"form_last5 updated: {form_updated} teams")
    finally:
        conn.close()
