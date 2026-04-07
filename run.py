#!/usr/bin/env python3
"""
Startup script — launches both the MCP sidecar and the Flask web app.
Run: python run.py
"""

import os
import sys
import threading
import time
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


def check_env():
    """Validate required environment variables."""
    from dotenv import load_dotenv
    load_dotenv()

    warnings = []
    errors = []

    if not os.getenv("OPENAI_API_KEY"):
        errors.append("OPENAI_API_KEY is not set")
    if not os.getenv("SQL_SERVER"):
        warnings.append("SQL_SERVER is not set (using 'localhost')")
    if not os.getenv("SQL_DATABASE"):
        warnings.append("SQL_DATABASE is not set (using 'master')")

    for w in warnings:
        logger.warning("⚠  %s", w)
    for e in errors:
        logger.error("✗  %s", e)

    if errors:
        logger.error("Fix the above errors in your .env file and restart.")
        sys.exit(1)


def start_mcp():
    """Start the MCP server in a background thread."""
    logger.info("🔌 Starting MCP server…")
    try:
        from mcp_server.server import run
        run()
    except Exception as exc:
        logger.error("MCP server crashed: %s", exc)


def start_flask():
    """Start the Flask web app."""
    from dotenv import load_dotenv
    load_dotenv()

    host = os.getenv("FLASK_HOST", "0.0.0.0")
    port = int(os.getenv("FLASK_PORT", "8000"))
    debug = os.getenv("FLASK_DEBUG", "false").lower() == "true"

    logger.info("🌐 Starting Flask app on http://%s:%s", host, port)
    logger.info("   Open http://localhost:%s in your browser", port)

    from app import app
    app.run(host=host, port=port, debug=debug, use_reloader=False)


if __name__ == "__main__":
    print("\n" + "="*60)
    print("   🔮 SQL AI Agent — Starting up")
    print("="*60 + "\n")

    check_env()

    # MCP in background daemon thread
    mcp_thread = threading.Thread(target=start_mcp, daemon=True)
    mcp_thread.start()

    # Give MCP a moment to bind
    time.sleep(1.0)

    # Flask in main thread
    start_flask()
