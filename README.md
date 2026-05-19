# Web-Automi

Web-Automi is a browser-enabled AI research agent with a FastAPI backend and a lightweight web dashboard. It can search the web, open live pages with Playwright, stream execution steps in real time, and save task history per user.

## What It Does

- Runs AI-assisted web research with live browser access
- Streams agent activity step by step over WebSockets
- Supports user accounts and session-based login
- Saves tasks and execution history in SQLite
- Uses search fallback logic, page navigation, and result summarization to improve reliability
- Supports multiple Groq API keys for rate-limit rotation

## Stack

- Backend: FastAPI, Uvicorn
- Agent runtime: Groq API
- Browser automation: Playwright, playwright-stealth
- Persistence: SQLite
- Frontend: HTML, Tailwind CSS, vanilla JavaScript
- Deployment: Docker, Render

## Local Setup

### Prerequisites

- Python 3.11+
- A `GROQ_API_KEY`
- Playwright Chromium browser

### Install

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
```

### Environment

Create a `.env` file with:

```env
GROQ_API_KEY=your_key_here
```

Optional variables:

```env
GROQ_API_KEY1=optional_fallback_key
GROQ_API_KEY2=optional_fallback_key
GROQ_API_BASE=optional_custom_base_url
HEADLESS=1
```

### Run

```bash
uvicorn server:app --reload
```

Open `http://localhost:8000/auth` to create an account or sign in.

## Docker

```bash
docker build -t web-automi .
docker run --rm -p 8000:10000 --env-file .env web-automi
```

The repository already includes a `Dockerfile` and `render.yaml` for deployment.

## Project Structure

```text
server.py          FastAPI app, auth, REST endpoints, WebSocket streaming
agent_runner.py    Background agent execution and step event capture
groq_chat.py       Tool-calling agent loop and final answer synthesis
browser_tools.py   Playwright search and navigation tools
database.py        SQLite persistence for users, tasks, and steps
static/            Frontend dashboard and auth pages
```

## Notes

- Local task history is stored in `chats.db`.
- On Render, SQLite is configured to use the mounted disk at `/app/data`.
- The main web UI is the primary interface, but the agent can also be run directly from `groq_chat.py`.
