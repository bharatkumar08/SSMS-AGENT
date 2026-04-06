"""
Flask Web Application
─────────────────────
Serves the UI and exposes a REST API that the frontend JS calls.
Spawns the MCP server as a background thread on startup.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from typing import Any

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request
from flask_cors import CORS

load_dotenv()

from agent import SQLAgent
from chart_generator import generate_chart

# ── App setup ─────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = os.getenv("SECRET_KEY", "dev-secret-key")
CORS(app)

# Singleton agent (initialised lazily)
_agent: SQLAgent | None = None


def get_agent() -> SQLAgent:
    global _agent
    if _agent is None:
        _agent = SQLAgent()
    return _agent


# ── Conversation store (in-memory, per-session keyed by session_id) ───────────
# For production, replace with Redis or a DB-backed store.
_conversations: dict[str, list[dict]] = {}


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/status")
def api_status():
    try:
        status = get_agent().status()
        return jsonify({"ok": True, **status})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/ask", methods=["POST"])
def api_ask():
    body = request.get_json(force=True, silent=True) or {}
    question = (body.get("question") or "").strip()
    session_id = body.get("session_id", "default")

    if not question:
        return jsonify({"error": "question is required"}), 400

    history = _conversations.get(session_id, [])

    try:
        agent = get_agent()
        result = agent.ask(question, conversation_history=history)

        # Generate chart
        chart_data = None
        if result.get("data"):
            chart_data = generate_chart(
                data=result["data"],
                columns=result["columns"],
                chart_spec=result.get("chart_spec", {}),
            )

        # Update conversation history
        history.append({"role": "user", "content": question})
        history.append({"role": "assistant", "content": result["answer"]})
        _conversations[session_id] = history[-20:]  # keep last 10 turns

        return jsonify({
            "answer": result["answer"],
            "sql_query": result.get("sql_query"),
            "columns": result.get("columns", []),
            "rows": result.get("data", [])[:int(os.getenv("MAX_ROWS_DISPLAY", "500"))],
            "row_count": len(result.get("data", [])),
            "chart_spec": result.get("chart_spec"),
            "chart_data": chart_data,
            "tool_calls": result.get("tool_calls", []),
            "duration_ms": result.get("duration_ms"),
            "error": result.get("error"),
        })

    except Exception as exc:
        logger.exception("Error processing question: %s", question)
        return jsonify({"error": str(exc)}), 500


@app.route("/api/clear", methods=["POST"])
def api_clear():
    body = request.get_json(force=True, silent=True) or {}
    session_id = body.get("session_id", "default")
    _conversations.pop(session_id, None)
    # Also clear agent schema cache
    global _agent
    if _agent:
        _agent._schema_cache = None
        _agent._tools_cache = None
    return jsonify({"ok": True})


@app.route("/api/schema")
def api_schema():
    try:
        from mcp_server.tools import db_manager
        schema = db_manager.get_schema(include_row_counts=True)
        return jsonify({"ok": True, "schema": schema})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/execute", methods=["POST"])
def api_execute():
    """Direct SQL execution endpoint (bypasses AI, for the SQL editor)."""
    body = request.get_json(force=True, silent=True) or {}
    query = (body.get("query") or "").strip()
    if not query:
        return jsonify({"error": "query is required"}), 400

    try:
        from mcp_server.tools import db_manager, handle_validate_sql_query
        validation = handle_validate_sql_query({"query": query})
        if not validation.get("valid"):
            return jsonify({"error": "Query validation failed", "issues": validation.get("issues")}), 400

        result = db_manager.execute_query(query, max_rows=int(os.getenv("MAX_ROWS_DISPLAY", "500")))
        chart_data = generate_chart(
            data=result["rows"],
            columns=result["columns"],
            chart_spec={"chart_type": "table", "title": "Query Results"},
        )
        return jsonify({**result, "chart_data": chart_data, "success": True})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ── MCP server sidecar ────────────────────────────────────────────────────────

def _start_mcp_server():
    """Launch the MCP server in a background daemon thread."""
    import time
    time.sleep(0.5)  # Let Flask bind first
    try:
        from mcp_server.server import run as mcp_run
        logger.info("Starting MCP server sidecar…")
        mcp_run()
    except Exception as exc:
        logger.error("MCP server failed to start: %s", exc)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Start MCP server in background
    mcp_thread = threading.Thread(target=_start_mcp_server, daemon=True)
    mcp_thread.start()

    host = os.getenv("FLASK_HOST", "0.0.0.0")
    port = int(os.getenv("FLASK_PORT", "5000"))
    debug = os.getenv("FLASK_DEBUG", "false").lower() == "true"

    logger.info("Starting Flask app on http://%s:%s", host, port)
    app.run(host=host, port=port, debug=debug, use_reloader=False)
