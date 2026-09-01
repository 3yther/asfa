"""Training-split review service — Sept 1-15 upper/lower cut.

Pulls the week's real data out of the existing tables (body_weight, meals,
gym_sessions, sleep, bench_progression) and rolls it into one weekly summary.
Single-user, like the rest of ASFA — the `user_id` argument in the dispatch
spec is accepted and ignored so the scheduler call site stays as written.
"""
from datetime import datetime, timedelta

import database as db


def _daterange(start: str, end: str):
    s = datetime.strptime(start, "%Y-%m-%d").date()
    e = datetime.strptime(end, "%Y-%m-%d").date()
    d = s
    while d <= e:
        yield d.strftime("%Y-%m-%d")
        d += timedelta(days=1)


def _split_nutrition_goals():
    """Grade nutrition against the SPLIT targets (1800 kcal / 175g protein), not
    the user's standing nutrition_goals row. Carbs/fat are left at 0, which
    score_nutrition_day treats as "no target set / met", so a day is judged on
    calories-in-band + protein-floor only — the two the split actually locks."""
    split = db.get_training_split()
    return {
        "calorie_goal": float(split["daily_calories"]),
        "protein_goal": float(split["daily_protein"]),
        "carbs_goal": 0.0,
        "fat_goal": 0.0,
    }


def _nutrition_adherence(start: str, end: str):
    """Share of logged days that graded A or B (on target) AGAINST SPLIT TARGETS.
    Returns (pct, days_on_target, days_logged)."""
    goals = _split_nutrition_goals()
    logged = 0
    on_target = 0
    for day in _daterange(start, end):
        totals = db.get_daily_macros(day)
        if totals["meal_count"] == 0:
            continue
        logged += 1
        grade = db.score_nutrition_day(day, totals=totals, goals=goals).get("grade")
        if grade in ("A", "B"):
            on_target += 1
    pct = round(100 * on_target / logged) if logged else 0
    return pct, on_target, logged


def _avg_sleep(start: str, end: str):
    by_day = db.get_sleep_hours_by_day(days=21)  # dict {date: hours}
    vals = [float(v) for d, v in by_day.items() if start <= d <= end and v]
    return round(sum(vals) / len(vals), 1) if vals else None


def generate_weekly_split_review(week_num: int = 1, user_id=None) -> dict:
    """Structured review for week `week_num` (1/2/3) of the active split."""
    week_num = max(1, min(3, int(week_num)))
    start, end = db.get_split_week_bounds(week_num)

    weight_start = db._weight_on_or_before(start)
    weight_end = db._weight_on_or_before(end)
    trend = db.get_weight_split_trend()
    week_row = next((w for w in trend["weeks"] if w["week"] == week_num), None)
    target_end = week_row["target_end"] if week_row else None
    on_pace = week_row["on_pace"] if week_row else None

    adherence_pct, days_on_target, days_logged = _nutrition_adherence(start, end)
    gym_sessions = db.count_gym_sessions_between(start, end)
    bench = db.get_bench_progression(end)
    sleep_avg = _avg_sleep(start, end)

    return {
        "week": week_num,
        "start_date": start,
        "end_date": end,
        "weight_start": round(weight_start, 1) if weight_start is not None else None,
        "weight_end": round(weight_end, 1) if weight_end is not None else None,
        "weight_target_end": target_end,
        "on_pace": on_pace,
        "nutrition_adherence": adherence_pct,
        "nutrition_days_on_target": days_on_target,
        "nutrition_days_logged": days_logged,
        "gym_sessions": gym_sessions,
        "bench_status": (f"1RM tested: {bench['1rm_value']}kg"
                         if bench["1rm_tested"] else bench["target"]),
        "bench_phase": bench["phase_label"],
        "sleep_avg_hours": sleep_avg,
        "notes": "",
    }
