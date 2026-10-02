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
│   │   ├── meta_model.py
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

- Frontend chat UI: `http://localhost:38080`
- Transformation dashboard: `http://localhost:38080/dashboard.html` (also the **Dashboard** button in the top bar)
- Backend health: `http://localhost:38000/api/health`
- Backend MCP tools list: `http://localhost:38000/api/tools`

## Workspace UI

The frontend is a buttons-and-preview workspace, not a chat-first UI:

- **Workspace tab**: upload a business process document or a requirement spec, review the extracted elements/relationships/view layout in an editable table and a diagram preview, then explicitly click "Create in Archi". Nothing is written to Archi until you approve the preview. Uncheck rows to drop elements or relationships, or edit element names inline before applying.
- **Health / MCP Tools tabs**: service status and the live MCP tool catalog.
- **Assistant drawer**: a collapsible chat panel docked on the right for free-form questions about the model. Collapse it with the chevron or the topbar toggle to give the workspace the full width.
- **Dashboard** (opens in a new tab): the steering view of the assessment — outcome KPIs, capability maturity heat map (TOGAF gap convention), capability gaps vs. plateaus, As-Is → To-Be traceability, application end of life and TIME portfolio, roadmap by plateau, gap register, and data completeness. Every figure is read live from the active Archi model via MCP (`GET /api/dashboard`) along the relationships of the governance meta-model; cost and project-progress data stay in the tools that own them. Property schema, metric definitions and the engineering demo dataset (`ops/seed_engineering_demo.py`): [docs/transformation-dashboard.md](docs/transformation-dashboard.md).

### Upload preview workflow

1. `POST /api/actions/business-process-upload/preview` or `POST /api/actions/requirements-upload/preview`
   - Input: multipart `file` (+ optional `view_name`)
   - Supports: `pdf`, `xlsx`, `xlsm`, `txt`, `md`, `csv`, `json`
   - Behavior: extracts elements/relationships/layout from the upload and returns an editable **plan** (nothing is written to Archi yet).
2. `POST /api/actions/apply`
   - Input: JSON body `{"plan": <plan from step 1, with any rows edited or unchecked>}`
   - Behavior: creates/updates the elements, relationships, and view content for the included rows in one automation run via MCP `bulk-mutate`.

Both preview endpoints still respect Archi approval mode. If approval mode is enabled, a human still needs to approve in Archi after `apply`.

### One-shot automation actions (no preview)

For scripted/API use without a review step, the original single-call endpoints are still available:

- `POST /api/actions/business-process-upload`
- `POST /api/actions/requirements-upload`

Same inputs as the preview endpoints above, but extraction and Archi writes happen in one call.

### Governance meta-model

`GET /api/meta-model` returns the organization's required ArchiMate element types, layers, and
relationship pairs (defined in `backend/app/meta_model.py`). This is injected into every extraction
prompt and the chat system prompt, so uploads and chat-driven model changes stick to the declared
vocabulary: relationships whose (source type, target type) pair isn't declared in the meta-model are
coerced to `AssociationRelationship` rather than left as an unsanctioned type. The "Meta model" button
in the UI renders this same data as a reference viewer.

### Extraction grounding (anti-hallucination)

Both upload actions require every extracted element/relationship to be backed by a quote from the
source document (`evidence` field) or a high model-reported confidence; candidates that fail both are
dropped and reported in the response's `warnings`. If everything fails the check, the request returns
`422` with an explanation instead of silently fabricating a result. For diagram/flowchart exports
specifically, sequential ("this step follows that step") relationships are only added when the model
explicitly flags the step as linearly sequential in the source text -- flattened diagram exports
usually don't carry that signal, so steps are created without a fabricated flow between them (visible
as a warning in the preview) rather than guessing at connections that aren't really there.

### Extraction model override

`AZURE_OPENAI_EXTRACTION_MODEL` (optional) lets uploads use a different, typically stronger, Azure
deployment than day-to-day chat (`AZURE_OPENAI_MODEL`), since extraction is reasoning-heavy and runs
far less often. Falls back to `AZURE_OPENAI_MODEL` when unset. The value must be an actual **deployment
name** that exists on your Azure OpenAI resource for chat completions -- check Azure AI Foundry /
Azure OpenAI Studio's Deployments list, not just the model catalog (a model can appear in the catalog
without having a working deployment).

### Chat with trace

POST `http://localhost:38000/api/chat/trace`

Returns normal chat output plus a detailed execution trace (rounds, retries, tool calls).

Example body:

```json
{
  "message": "Summarize the architecture and mention key dependencies.",
  "history": [],
  "system_prompt": null
}
```

### Streaming chat (SSE)

POST `http://localhost:38000/api/chat/stream`

Streams response chunks as Server-Sent Events with event names:

- `start`
- `delta` (text chunks)
- `trace` (optional full trace + tools)
- `done`
- `error`
- `close`

Example body:

```json
{
  "message": "What application components support customer sales?",
  "history": [],
  "stream_chunk_chars": 180,
  "include_trace": true
}
```

### Conversation export

POST `http://localhost:38000/api/conversations/export`

Validates and normalizes a conversation payload for backup or transfer.

### Conversation import

POST `http://localhost:38000/api/conversations/import`

Accepts either:

- full export envelope (`schema_version` + `conversation`)
- raw `conversation` object

Returns normalized conversation + warnings if any fields were corrected.

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

- Upload says no readable text:
  - For scanned PDFs, run OCR first.
  - For legacy Excel `.xls`, convert to `.xlsx` and retry.

- App loads but new endpoints/features consistently 404 or hang, even right after a successful
  rebuild (verified working via `docker compose exec` or `curl` from inside WSL, but not from a
  Windows browser at `http://localhost:<port>`) -- two distinct root causes have hit this app on
  this machine, both presenting identically:
  - **Orphaned `docker-proxy` processes** surviving `docker compose down` (visible via
    `ps -ef | grep docker-proxy` inside WSL, usually much older than the current containers). They
    keep holding the old host port and silently swallow requests to newly added routes.
    `docker system prune -f` does not clean these up, and killing them requires root.
  - **A second, independent Docker engine also serving the same port.** If Docker Desktop is
    installed, it runs containers inside its own separate WSL VM (`wsl --list --verbose` shows it
    as `docker-desktop`), completely independent from a plain WSL distro's native Docker engine.
    If a copy of this app was ever built/started while Docker Desktop's engine was the active
    `docker context`, that old copy can keep running and keep answering on the same port forever,
    invisible to `docker compose ps` run against the native engine. Diagnose from **Windows**
    PowerShell (not WSL) with `netstat -ano | findstr :<port>` to get the PID, then
    `tasklist | findstr <PID>` -- if that PID is `wslrelay.exe`, check `wsl --list --verbose` for a
    second running distro and check Docker Desktop's own dashboard for a stale container.
  - Workaround already applied each time this has recurred: pick a fresh, previously-unused port
    pair in `docker-compose.yml` and rebuild. The app has moved from `8000`/`8080`, to
    `18000`/`18080`, to `28000`/`28080`, and now to `38000`/`38080`. A fresh port number always
    dodges whatever is squatting on the old one, regardless of which of the two causes above it is.
