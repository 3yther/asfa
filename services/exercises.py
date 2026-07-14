"""Exercise library — the browsable catalogue at /gym/exercises.

Serves the 1,324-exercise catalogue (synced from hasaneyldrm/exercises-dataset
by scripts/sync_exercises.py into the ``exercises`` table) as a filtered,
paginated read API plus the page itself. This is separate from the curated
gym_exercises library that drives logging and ranks; the only bridge is
"Add to workout", which get_or_create's a gym_exercises row from a catalogue
entry so it can be logged through the normal gym flow.

Auth: every route here is session-gated by ASFA's global before_request (none
are in _PUBLIC_ENDPOINTS). The one POST (/add-to-workout) carries the CSRF
token via the patched fetch wrapper included through nav.html on the page.
"""
from flask import Blueprint, jsonify, render_template, request

import database as db

exercises_bp = Blueprint("exercises", __name__)

# Catalogue equipment values we treat as cardio when bridging into the gym log.
_CARDIO_EQUIPMENT = {"stationary bike", "elliptical machine", "stepmill machine",
                     "skierg machine", "upper body ergometer"}


def _truthy(value: str) -> bool:
    return str(value).lower() in ("1", "true", "yes", "on")


@exercises_bp.route("/gym/exercises")
def exercises_page():
    return render_template("gym-exercises.html", active="gym")


@exercises_bp.route("/api/exercises")
def api_exercises():
    """Filtered + paginated catalogue. Query params: category, equipment
    (comma-separated), home_only, difficulty, q (name search), page, per_page."""
    result = db.get_exercises(
        category=request.args.get("category") or None,
        equipment=request.args.get("equipment") or None,
        home_only=_truthy(request.args.get("home_only", "")),
        q=request.args.get("q") or None,
        difficulty=request.args.get("difficulty") or None,
        page=request.args.get("page", 1),
        per_page=request.args.get("per_page", 48),
    )
    return jsonify(result)


@exercises_bp.route("/api/exercises/facets")
def api_exercise_facets():
    """Distinct categories / equipment / difficulties for the filter sidebar."""
    return jsonify(db.get_exercise_facets())


@exercises_bp.route("/api/exercises/<ex_id>")
def api_exercise(ex_id):
    ex = db.get_exercise_by_id(ex_id)
    if not ex:
        return jsonify({"error": "exercise not found"}), 404
    return jsonify(ex)


@exercises_bp.route("/api/exercises/<ex_id>/add-to-workout", methods=["POST"])
def api_add_to_workout(ex_id):
    """Bridge a catalogue exercise into the loggable gym_exercises library and
    return the gym_exercises row. The frontend then hands it to the existing
    gym session flow (localStorage handoff → gym.js). No workout state is
    mutated server-side here — sets are still logged via /api/gym/sets."""
    ex = db.get_exercise_by_id(ex_id)
    if not ex:
        return jsonify({"error": "exercise not found"}), 404
    equipment = (ex.get("equipment") or "").strip().lower()
    exercise_type = "cardio" if equipment in _CARDIO_EQUIPMENT else "strength"
    gym_ex = db.get_or_create_gym_exercise(
        name=ex["name"],
        muscle_group=ex.get("category") or ex.get("target_muscle"),
        equipment=ex.get("equipment"),
        exercise_type=exercise_type,
        instructions=ex.get("instructions"),
    )
    return jsonify({"ok": True, "gym_exercise": gym_ex})
