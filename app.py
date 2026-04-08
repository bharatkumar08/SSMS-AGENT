"""
Flask Web Application
─────────────────────
Serves the UI and exposes a REST API that the frontend JS calls.
On Azure, the MCP server runs as a SEPARATE App Service — all DB access
goes through HTTP calls to MCP_URL, never via direct imports.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx
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

# MCP server URL — must be set in Azure Flask App Service → Configuration → App Settings
# e.g. MCP_URL=https://your-mcp-appservice.azurewebsites.net/rpc
MCP_URL = os.getenv(
    "MCP_URL",
    "https://mcp-sql-epf8dhbzf4c4f2ej.centralindia-01.azurewebsites.net/rpc"
)

# Singleton agent (initialised lazily)
_agent: SQLAgent | None = None


def get_agent() -> SQLAgent:
    global _agent
    if _agent is None:
        _agent = SQLAgent()
    return _agent


# ── MCP HTTP helper ───────────────────────────────────────────────────────────

def call_mcp(tool_name: str, arguments: dict) -> dict:
    """Call a tool on the MCP server over HTTP and return the parsed result dict."""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments},
    }
    try:
        response = httpx.post(MCP_URL, json=payload, timeout=30)
        response.raise_for_status()
        data = response.json()
        if "error" in data:
            raise RuntimeError(f"MCP error: {data['error']['message']}")
        # Unpack MCP content envelope → dict
        content = data.get("result", {}).get("content", [])
        if content and content[0].get("type") == "text":
            return json.loads(content[0]["text"])
        return data.get("result", {})
    except httpx.ConnectError:
        raise RuntimeError(
            f"Cannot reach MCP server at {MCP_URL}. "
            "Check MCP_URL is set correctly in Azure App Settings."
        )


# ── Conversation store (in-memory, per-session keyed by session_id) ───────────
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
    global _agent
    if _agent:
        _agent._schema_cache = None
        _agent._tools_cache = None
    return jsonify({"ok": True})


@app.route("/api/schema")
def api_schema():
    """Fetch schema via MCP server over HTTP — no direct DB import."""
    try:
        result = call_mcp("get_database_schema", {"include_row_counts": True})
        if not result.get("success"):
            raise RuntimeError(result.get("error", "Unknown MCP error"))
        return jsonify({"ok": True, "schema": result["schema"]})
    except Exception as exc:
        logger.exception("api_schema failed")
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/execute", methods=["POST"])
def api_execute():
    """Direct SQL execution via MCP server — no direct DB import."""
    body = request.get_json(force=True, silent=True) or {}
    query = (body.get("query") or "").strip()
    if not query:
        return jsonify({"error": "query is required"}), 400

    try:
        # Step 1: Validate
        validation = call_mcp("validate_sql_query", {"query": query})
        if not validation.get("valid"):
            return jsonify({
                "error": "Query validation failed",
                "issues": validation.get("issues"),
            }), 400

        # Step 2: Execute
        result = call_mcp("execute_sql_query", {
            "query": query,
            "max_rows": int(os.getenv("MAX_ROWS_DISPLAY", "500")),
        })
        if not result.get("success"):
            return jsonify({"error": result.get("error", "Query failed")}), 500

        chart_data = generate_chart(
            data=result["rows"],
            columns=result["columns"],
            chart_spec={"chart_type": "table", "title": "Query Results"},
        )
        return jsonify({**result, "chart_data": chart_data, "success": True})

    except Exception as exc:
        logger.exception("api_execute failed")
        return jsonify({"error": str(exc)}), 500


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # NOTE: MCP server is a SEPARATE Azure App Service — do NOT start it here.
    # Flask communicates with it via MCP_URL env var.
    host = os.getenv("FLASK_HOST", "0.0.0.0")
    port = int(os.getenv("PORT") or os.getenv("FLASK_PORT", "5000"))  # Azure injects PORT
    debug = os.getenv("FLASK_DEBUG", "false").lower() == "true"

    logger.info("Starting Flask app on http://%s:%s (MCP_URL=%s)", host, port, MCP_URL)
    app.run(host=host, port=port, debug=debug, use_reloader=False)
