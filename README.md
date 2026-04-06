# 🔮 SQL AI Agent

A production-ready AI agent that connects to **Microsoft SQL Server**, accepts **natural language questions**, converts them to **T-SQL**, executes the queries, and presents results with **interactive charts** in a modern web UI.

---

## Architecture

```
┌──────────────┐        ┌────────────────────┐        ┌──────────────────────┐
│  Browser UI  │◄──────►│   Flask Web App    │◄──────►│   OpenAI GPT-4o      │
│  (Plotly.js) │        │    (app.py)        │        │  (function calling)  │
└──────────────┘        └────────┬───────────┘        └──────────────────────┘
                                 │ HTTP JSON-RPC
                                 ▼
                        ┌────────────────────┐        ┌──────────────────────┐
                        │   MCP Server       │◄──────►│  SQL Server (SSMS)   │
                        │  (mcp_server/)     │        │  via pyodbc/sqlalchemy│
                        └────────────────────┘        └──────────────────────┘
```

### Components

| File | Role |
|------|------|
| `run.py` | Entry point — starts both servers |
| `app.py` | Flask web app + REST API |
| `agent.py` | AI agent orchestration loop (OpenAI ↔ MCP) |
| `chart_generator.py` | Plotly chart generation from query results |
| `mcp_server/server.py` | MCP HTTP server (JSON-RPC 2.0) |
| `mcp_server/tools.py` | SQL tool definitions + handlers |
| `templates/index.html` | Single-page web UI |

### MCP Tools Exposed

| Tool | Description |
|------|-------------|
| `get_database_schema` | Full schema: tables, columns, PKs, FKs, row counts |
| `execute_sql_query` | Run a SELECT and return rows + column metadata |
| `get_table_sample` | Sample rows from a table |
| `validate_sql_query` | Check SQL for syntax/safety errors before running |

---

## Quick Start

### 1. Prerequisites

- **Python 3.10 – 3.13** recommended  
  *(Python 3.14 is too new for some pre-built wheels — use 3.12 for best compatibility)*  
  Check with: `python --version`
- Microsoft ODBC Driver 17 (or 18) for SQL Server
  - [Download here](https://learn.microsoft.com/en-us/sql/connect/odbc/download-odbc-driver-for-sql-server)
- Access to a SQL Server instance (SSMS, Azure SQL, etc.)
- OpenAI API key with access to GPT-4o

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure environment

```bash
cp .env.example .env
# Edit .env with your values:
```

```env
OPENAI_API_KEY=sk-your-key-here
OPENAI_MODEL=gpt-4o

SQL_SERVER=your-server-name\instance   # or IP address
SQL_DATABASE=your-database
SQL_USERNAME=your-username
SQL_PASSWORD=your-password
SQL_DRIVER=ODBC Driver 17 for SQL Server

# Windows auth instead of SQL auth:
# SQL_TRUSTED_CONNECTION=1
```

### 4. Run

```bash
python run.py
```

Open **http://localhost:5000** in your browser.

---

## Usage

### Natural Language Chat

Type questions like:
- *"Show me total sales by product category for Q4 2023"*
- *"Which customers haven't placed an order in the last 6 months?"*
- *"What are the top 10 most expensive products in stock?"*
- *"Show me a trend of daily order counts over the past year"*

The agent will:
1. Inspect your database schema
2. Write an appropriate T-SQL query
3. Execute it safely (SELECT only)
4. Return results with a chart if appropriate

### SQL Editor

Use the **SQL Editor** tab to write and execute T-SQL directly with syntax highlighting and results visualisation.

---

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | Web UI |
| GET | `/api/status` | Agent + MCP server health |
| POST | `/api/ask` | Natural language query |
| POST | `/api/execute` | Direct SQL execution |
| GET | `/api/schema` | Full database schema |
| POST | `/api/clear` | Clear conversation history |

### POST /api/ask

```json
{
  "question": "Show me monthly revenue for 2023",
  "session_id": "optional-session-id"
}
```

Response:
```json
{
  "answer": "### 📊 Results Summary\n...",
  "sql_query": "SELECT MONTH(OrderDate) AS Month, SUM(Total) AS Revenue ...",
  "columns": ["Month", "Revenue"],
  "rows": [...],
  "row_count": 12,
  "chart_spec": { "chart_type": "bar", "x_column": "Month", ... },
  "chart_data": { /* Plotly figure JSON */ },
  "tool_calls": [...],
  "duration_ms": 2341
}
```

---

## Security Notes

- Only **SELECT** and **WITH** (CTE) statements are permitted — all other DML/DDL is blocked
- Queries are validated before execution
- No direct SQL injection vector — the AI generates queries that go through the validator
- For production: use a **read-only SQL login** with minimal permissions
- Consider adding authentication middleware in front of Flask for production deployments

---

## Customisation

### Add more suggested questions

Edit the chips in `templates/index.html`:
```html
<div class="chip" data-q="Your question here">Label</div>
```

### Change AI model

In `.env`:
```env
OPENAI_MODEL=gpt-4o-mini   # cheaper, faster
OPENAI_MODEL=gpt-4-turbo   # older model
```

### Limit result rows

```env
MAX_ROWS_DISPLAY=200
QUERY_TIMEOUT=60
```

### Deploy with gunicorn

```bash
pip install gunicorn
# Start MCP server first
python -m mcp_server.server &
# Then serve Flask with gunicorn
gunicorn app:app --bind 0.0.0.0:5000 --workers 2 --timeout 120
```

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `pydantic-core` build error / Rust required | You're on Python 3.14 — pre-built wheels don't exist yet. Switch to Python 3.12: `py -3.12 -m pip install -r requirements.txt` |
| `metadata-generation-failed` on any package | Same root cause — use Python 3.12 |
| `pyodbc.Error: [08001]` | Wrong server name, driver not installed, or firewall blocking port 1433 |
| `Authentication failed` | Check SQL_USERNAME / SQL_PASSWORD; try SQL_TRUSTED_CONNECTION=1 for Windows auth |
| OpenAI `AuthenticationError` | Double-check OPENAI_API_KEY |
| Charts not rendering | Ensure browser can load Plotly.js CDN |