# Archi Local Chatbot (Option 1)

Local web app with separate frontend + backend:

- Frontend: static chat UI via `nginx`
- Backend: `FastAPI` service
- LLM: Azure OpenAI (your tenant API key)
- Tooling: your running Archi MCP server (`fanievh/archi-mcp-server`)

## Folder Structure

```text
archi-local-chatbot/
├── backend/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── azure_agent.py
│   │   ├── config.py
│   │   ├── main.py
│   │   ├── mcp_client.py
│   │   └── schemas.py
│   ├── Dockerfile
│   └── requirements.txt
├── frontend/
│   ├── app.js
│   ├── Dockerfile
│   ├── index.html
│   ├── nginx.conf
│   └── style.css
├── ops/
│   └── check_mcp.sh
├── .env.example
├── .gitignore
├── docker-compose.yml
└── README.md
```

## Prerequisites

1. Archi is running with MCP plugin and server started.
2. Azure OpenAI deployment exists and API key is available.
3. Docker Desktop is running.

## Configure

```bash
cd /home/aburkart/Archi/archi-local-chatbot
cp .env.example .env
```

Edit `.env`:

- `AZURE_OPENAI_API_KEY`
- `AZURE_OPENAI_BASE_URL`
- `AZURE_OPENAI_MODEL`
- `MCP_SERVER_URL`
  - Docker default for host-side Archi: `http://host.docker.internal:18090/mcp`
- `MCP_BEARER_TOKEN` if enabled

## Run with Docker

```bash
docker compose up --build -d
```

Open:

- Frontend chat UI: `http://localhost:8080`
- Backend health: `http://localhost:8000/api/health`
- Backend MCP tools list: `http://localhost:8000/api/tools`

## Stop

```bash
docker compose down
```

## Local (without Docker)

```bash
cd /home/aburkart/Archi/archi-local-chatbot/backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export $(grep -v '^#' ../.env | xargs)
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Then serve frontend separately or open a static server and point calls to `/api` through proxy.

## Troubleshooting

- `mcp_status=error` in `/api/health`:
  - Verify Archi MCP server is started.
  - Verify MCP URL/token in `.env`.
- Docker cannot reach Archi MCP:
  - keep `MCP_SERVER_URL=http://host.docker.internal:18090/mcp`
  - restart Docker Desktop after network changes.
- Azure auth/model errors:
  - verify key, base URL, deployment/model name.

