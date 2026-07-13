"""Interview Assistant — merged into ASFA as an in-app page at /interview.

Originally a standalone Flask + Flask-SocketIO app (~/interview_assistant/). The
live-audio transcription path (WebSocket audio → faster-whisper) is intentionally
NOT carried over: it can't run on ASFA's Railway deployment (no FFmpeg/Whisper,
and gunicorn's sync worker doesn't serve WebSockets). What remains — Practice
mode and History — is wired over plain HTTP so it runs under ASFA's existing
gunicorn setup with no worker-class or monkeypatch changes. Live audio stays
available only in the standalone app run locally.

Auth: every route here is session-gated by ASFA's global before_request (none are
in _PUBLIC_ENDPOINTS). POSTs/DELETEs carry the CSRF token via the patched fetch
wrapper included through nav.html/_csrf.html on the page; same-origin cookies
carry the session automatically.

Persistence: interview_sessions / interview_qa via database.py (SQLite locally,
Postgres on Railway).
"""
import json
import os
from datetime import datetime

from flask import (Blueprint, Response, jsonify, render_template, request,
                   stream_with_context)

import database as db

interview_bp = Blueprint("interview", __name__)

# Answer model. The standalone app's old claude-3-5-sonnet-20241022 is retired,
# so default to current Sonnet 5 (balanced quality/latency for spoken-answer
# drafting). Override with INTERVIEW_MODEL (or the shared CLAUDE_MODEL) if needed.
_MODEL = os.environ.get("INTERVIEW_MODEL") or os.environ.get(
    "CLAUDE_MODEL", "claude-sonnet-5")

_client = None


def _get_client():
    """Lazily construct the Anthropic client so importing this module never fails
    when ANTHROPIC_API_KEY is unset (e.g. local dev without a key)."""
    global _client
    if _client is None:
        from anthropic import Anthropic
        _client = Anthropic()
    return _client


def _build_system_prompt(resume, role, jobdesc):
    """Ground answers in the candidate's real CV + the target role (verbatim from
    the standalone app so answer quality is unchanged)."""
    parts = [
        f"You are helping a candidate answer live questions in a {role} interview.",
        "Answer in first person as the candidate. Be concise, concrete, and confident.",
    ]
    if resume:
        parts.append("\nThe candidate's background (ground every answer in this — "
                     "cite real projects, skills, and experience from it):\n" + resume[:6000])
    if jobdesc:
        parts.append("\nThe role they're interviewing for:\n" + jobdesc[:2000])
    parts.append("\nNever invent experience not in the background. If the background "
                 "doesn't cover something, answer honestly and generally.")
    return "\n".join(parts)


@interview_bp.route("/")
def index():
    return render_template("interview.html", active="interview")


@interview_bp.route("/api/upload_resume", methods=["POST"])
def upload_resume():
    """Extract text from an uploaded CV (PDF via pypdf, else decode as UTF-8).
    Returns the text so the client can hold it and send it with each question —
    there's no server-side session in the HTTP-only model."""
    f = request.files.get("file")
    if not f:
        return jsonify({"ok": False, "error": "no file"}), 400
    fname = (f.filename or "").lower()
    if fname.endswith(".pdf"):
        try:
            from pypdf import PdfReader
            reader = PdfReader(f.stream)
            text = "\n".join((p.extract_text() or "") for p in reader.pages)
        except Exception as e:
            return jsonify({"ok": False, "error": f"pdf parse failed: {e}"}), 400
    else:
        text = f.stream.read().decode("utf-8", errors="ignore")
    return jsonify({"ok": True, "text": text.strip()})


@interview_bp.route("/api/practice", methods=["POST"])
def practice():
    """Stream a grounded answer to a practice question over Server-Sent Events,
    then persist the Q&A. Creates a session on the first question and echoes the
    session_id back so follow-ups land in the same session.

    SSE (not WebSockets) is deliberate: it's a plain streaming HTTP response, so
    it works under ASFA's gunicorn sync worker with no async/monkeypatch changes.
    """
    data = request.get_json(force=True, silent=True) or {}
    question = (data.get("question") or "").strip()
    if not question:
        return jsonify({"ok": False, "error": "no question"}), 400
    resume = (data.get("resume") or "").strip()
    role = (data.get("role") or "Software Engineer").strip()
    jobdesc = (data.get("jobdesc") or "").strip()
    session_id = data.get("session_id") or db.interview_new_session(role, "practice")

    ts = datetime.now().strftime("%H:%M:%S")
    system = _build_system_prompt(resume, role, jobdesc)
    user = (f'Interview practice question: "{question}"\n\n'
            "Give me a strong first-person answer I can say out loud. "
            "2-4 sentences, specific, confident, no filler.")

    def sse(event, payload):
        return f"event: {event}\ndata: {json.dumps(payload)}\n\n"

    @stream_with_context
    def generate():
        yield sse("start", {"session_id": session_id, "ts": ts, "question": question})
        full = ""
        try:
            client = _get_client()
            with client.messages.stream(model=_MODEL, max_tokens=400, system=system,
                                        messages=[{"role": "user", "content": user}]) as stream:
                for delta in stream.text_stream:
                    full += delta
                    yield sse("delta", {"delta": delta})
        except Exception as e:  # surface the error into the stream, don't 500 mid-body
            yield sse("error", {"error": str(e)})
            return
        qa_id = db.interview_save_qa(session_id, question, full, ts)
        yield sse("done", {"qa_id": qa_id, "session_id": session_id})

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@interview_bp.route("/api/sessions")
def list_sessions():
    return jsonify(db.interview_list_sessions())


@interview_bp.route("/api/sessions/<int:sid>")
def session_detail(sid):
    detail = db.interview_session_detail(sid)
    if not detail:
        return jsonify({"ok": False, "error": "not found"}), 404
    return jsonify({"ok": True, **detail})


@interview_bp.route("/api/sessions/<int:sid>", methods=["DELETE"])
def delete_session(sid):
    db.interview_delete_session(sid)
    return jsonify({"ok": True})


@interview_bp.route("/api/rate", methods=["POST"])
def rate():
    data = request.get_json(force=True, silent=True) or {}
    qa_id = data.get("qa_id")
    try:
        rating = int(data.get("rating", 0))
    except (TypeError, ValueError):
        rating = 0
    if qa_id and 1 <= rating <= 5:
        db.interview_rate(qa_id, rating)
        return jsonify({"ok": True, "qa_id": qa_id, "rating": rating})
    return jsonify({"ok": False, "error": "bad rating"}), 400
