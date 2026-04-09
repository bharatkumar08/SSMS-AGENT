"""
AI Agent Core
─────────────
Orchestrates the conversation between the user, OpenAI GPT, and the MCP
server.  Each user question kicks off a tool-use loop:

  1. Ask GPT what tools to call (using function-calling / tool-use API)
  2. Call those tools on the MCP server
  3. Feed results back to GPT
  4. Repeat until GPT produces a final answer (no more tool calls)
  5. Return the SQL, data, and a chart spec to the Flask app
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

import httpx
from openai import AzureOpenAI
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


# ── MCP client ────────────────────────────────────────────────────────────────

class MCPClient:
    """Thin HTTP client that speaks JSON-RPC to the MCP server."""

    def __init__(self):
        host = os.getenv("MCP_HOST", "mcp-sql-epf8dhbzf4c4f2ej.centralindia-01.azurewebsites.net")
        port = int(os.getenv("MCP_PORT", "8000"))
        self.base_url = "https://mcp-sql-epf8dhbzf4c4f2ej.centralindia-01.azurewebsites.net"
        self._id = 0

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    def _call(self, method: str, params: dict | None = None) -> Any:
        payload = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": method,
            "params": params or {},
        }
        try:
            resp = httpx.post(
                f"{self.base_url}/rpc",
                json=payload,
                timeout=int(os.getenv("QUERY_TIMEOUT", "30")),
            )
            resp.raise_for_status()
            data = resp.json()
            if "error" in data:
                raise RuntimeError(f"MCP error: {data['error']}")
            return data.get("result")
        except httpx.ConnectError:
            raise RuntimeError(
                "Cannot reach MCP server. Is it running? "
                f"(expected at {self.base_url})"
            )

    def list_tools(self) -> list[dict]:
        result = self._call("tools/list")
        return result.get("tools", [])

    def call_tool(self, name: str, arguments: dict) -> Any:
        result = self._call("tools/call", {"name": name, "arguments": arguments})
        # Unpack MCP content envelope
        if isinstance(result, dict) and "content" in result:
            content = result["content"]
            if content and content[0].get("type") == "text":
                return json.loads(content[0]["text"])
        return result

    def ping(self) -> bool:
        try:
            self._call("ping")
            return True
        except Exception:
            return False


# ── System prompt ─────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are an expert SQL Server data analyst assistant.

Your job is to help users query a Microsoft SQL Server database using natural language.

## Workflow (MUST follow this order):
1. ALWAYS call `get_database_schema` first (unless schema was already retrieved in this session)
2. Analyse the schema to identify relevant tables/columns
3. Optionally call `get_table_sample` to understand data shape
4. Optionally call `validate_sql_query` to verify your SQL before execution
5. Call `execute_sql_query` with a well-formed T-SQL SELECT statement
6. Summarise the results clearly for the user

## SQL Rules:
- Use only SELECT / WITH (CTEs) statements — never INSERT, UPDATE, DELETE, DROP, etc.
- Always qualify column names with table aliases when joining tables
- Use TOP N to limit large result sets (default TOP 500 unless user asks for all)
- Prefer meaningful column aliases
- Format SQL with proper indentation

## Response format (IMPORTANT):
When you have query results, your final message MUST include these sections:

### 📊 Results Summary
<plain-English summary of what the data shows>

### 🔍 SQL Query Used
```sql
<the exact query that was executed>
```

### 📈 Chart Recommendation
<JSON object — output ONLY valid JSON, no prose>
{
  "chart_type": "bar|line|pie|scatter|table",
  "x_column": "<column name for x-axis or labels>",
  "y_column": "<column name for y-axis or values>",
  "title": "<descriptive chart title>",
  "reasoning": "<one sentence why this chart type suits the data>"
}

If the result is not suitable for a chart, set chart_type to "table".
"""


# ── Agent ─────────────────────────────────────────────────────────────────────

class SQLAgent:
    """Agentic loop: GPT ↔ MCP tools → structured response."""

    def __init__(self):
        self.client = AzureOpenAI(
            api_key=os.getenv("AZURE_OPENAI_API_KEY"),
            api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-08-01-preview"),
            azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
        )
        self.model = os.getenv("AZURE_OPENAI_DEPLOYMENT")
        self.mcp = MCPClient()
        self._tools_cache: list[dict] | None = None
        self._schema_cache: dict | None = None

    # ── Tool schema conversion ────────────────────────────────────────────────

    def _get_openai_tools(self) -> list[dict]:
        """Convert MCP tool list to OpenAI function-calling format."""
        if self._tools_cache is not None:
            return self._tools_cache
        mcp_tools = self.mcp.list_tools()
        self._tools_cache = [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["inputSchema"],
                },
            }
            for t in mcp_tools
        ]
        return self._tools_cache

    # ── Tool execution ────────────────────────────────────────────────────────

    def _execute_tool(self, name: str, arguments: dict) -> str:
        """Call the MCP server and return result as JSON string."""
        logger.info("Tool call: %s(%s)", name, list(arguments.keys()))
        result = self.mcp.call_tool(name, arguments)
        return json.dumps(result, default=str)

    # ── Main agentic loop ─────────────────────────────────────────────────────

    def ask(self, question: str, conversation_history: list[dict] | None = None) -> dict:
        """
        Process a natural-language question and return a structured response.

        Returns:
            {
                "answer": str,           # GPT's final answer (markdown)
                "sql_query": str | None, # extracted SQL
                "data": list[dict],      # query result rows
                "columns": list[str],    # column names
                "chart_spec": dict,      # chart recommendation
                "tool_calls": list,      # audit trail of tool calls
                "error": str | None,     # if something went wrong
            }
        """
        start = time.time()
        tools = self._get_openai_tools()
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]

        # Inject conversation history for multi-turn
        if conversation_history:
            messages.extend(conversation_history[-10:])  # last 5 turns

        messages.append({"role": "user", "content": question})

        tool_call_log: list[dict] = []
        data: list[dict] = []
        columns: list[str] = []
        sql_query: str | None = None
        max_iterations = 10

        for iteration in range(max_iterations):
            logger.info("Agent iteration %d/%d", iteration + 1, max_iterations)

            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=tools,
                tool_choice="auto",
                temperature=0,
            )

            choice = response.choices[0]
            message = choice.message
            messages.append(message.model_dump(exclude_none=True))

            # No tool calls → we have the final answer
            if choice.finish_reason == "stop" or not message.tool_calls:
                answer = message.content or ""
                sql_query, chart_spec = _parse_final_answer(answer)
                return {
                    "answer": answer,
                    "sql_query": sql_query,
                    "data": data,
                    "columns": columns,
                    "chart_spec": chart_spec,
                    "tool_calls": tool_call_log,
                    "duration_ms": int((time.time() - start) * 1000),
                    "error": None,
                }

            # Execute tool calls
            for tc in message.tool_calls:
                fn_name = tc.function.name
                try:
                    fn_args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    fn_args = {}

                result_str = self._execute_tool(fn_name, fn_args)
                result_dict = json.loads(result_str)

                tool_call_log.append({
                    "tool": fn_name,
                    "arguments": fn_args,
                    "result_preview": result_str[:300],
                })

                # Cache query data for chart generation
                if fn_name == "execute_sql_query" and result_dict.get("success"):
                    data = result_dict.get("rows", [])
                    columns = result_dict.get("columns", [])
                    sql_query = fn_args.get("query")

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result_str,
                })

        # Safety fallback
        return {
            "answer": "I reached the maximum number of reasoning steps. Please rephrase your question.",
            "sql_query": sql_query,
            "data": data,
            "columns": columns,
            "chart_spec": {"chart_type": "table"},
            "tool_calls": tool_call_log,
            "duration_ms": int((time.time() - start) * 1000),
            "error": "max_iterations_reached",
        }

    def status(self) -> dict:
        mcp_ok = self.mcp.ping()
        return {
            "openai_model": self.model,
            "mcp_server": "connected" if mcp_ok else "disconnected",
            "mcp_url": self.mcp.base_url,
        }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_final_answer(text: str) -> tuple[str | None, dict]:
    """Extract SQL and chart spec from the GPT final answer."""
    import re

    sql_query = None
    chart_spec = {"chart_type": "table"}

    # Extract SQL from ```sql ... ``` blocks
    sql_match = re.search(r"```sql\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if sql_match:
        sql_query = sql_match.group(1).strip()

    # Extract JSON chart spec
    json_match = re.search(r"(\{[^{}]*\"chart_type\"[^{}]*\})", text, re.DOTALL)
    if json_match:
        try:
            chart_spec = json.loads(json_match.group(1))
        except json.JSONDecodeError:
            pass

    return sql_query, chart_spec
