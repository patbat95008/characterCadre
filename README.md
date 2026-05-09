# CharacterCadre

Browser-based local RPG roleplay app powered by [Ollama](https://ollama.com/). Runs entirely on your own hardware — no cloud services or API keys required.

Features a multi-phase AI game loop with a Director (scene steering), DM (narration), Companion characters (in-character responses), and Player option drafting. Includes full editors for characters and scenarios, save/load support, and automatic context summarisation to stay within model token limits.

## Prerequisites

- Python 3.11+
- Node 18+
- [Ollama](https://ollama.com/) installed

## Setup

**Backend**

```bash
cd backend
python -m venv .venv

# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate

pip install -e ".[dev]"
```

**Frontend**

```bash
cd frontend
npm install
```

**Pull a model**

```bash
ollama pull mistral-small3.1
```

## Running

The easiest way is the unified launcher, which starts Ollama, the backend, and the frontend in a single terminal:

```bash
python launch.py
```

Add `--debug` to enable LLM request/response logging to `logs/`.

Alternatively, start each service manually in separate terminals:

```bash
# Terminal 1 — backend
cd backend
uvicorn app.main:app --reload --port 8000

# Terminal 2 — frontend
cd frontend
npm run dev
```

Open [http://localhost:5173](http://localhost:5173) in your browser.

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `OLLAMA_MODEL` | `mistral-small3.1` | Ollama model name |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama API base URL |
| `OLLAMA_STREAM_IDLE_SECONDS` | `20` | Per-token idle timeout for streaming calls |
| `OLLAMA_STRUCTURED_TIMEOUT_SECONDS` | `30` | Total timeout for structured (non-streaming) calls |
| `LOG_LEVEL` | `INFO` | Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `DATA_DIR` | `data` | Path to data directory (relative to `backend/`) |
| `PORT` | `8000` | Port (used in startup banner only; uvicorn sets the actual port) |

Set via shell before starting the backend:

```bash
set LOG_LEVEL=DEBUG    # Windows
export LOG_LEVEL=DEBUG # macOS/Linux
```

## Running Tests

```bash
cd backend
pytest tests/unit/         # fast, no running server needed
pytest tests/integration/  # requires Ollama running
pytest tests/resilience/   # error-handling and edge cases
pytest tests/              # all of the above
```

## Project Structure

```
charactercadre/
├── launch.py                    # Unified launcher (Ollama + backend + frontend)
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI entrypoint
│   │   ├── models.py            # Pydantic domain models
│   │   ├── phases.py            # Multi-phase game loop
│   │   ├── prompt_builder.py    # Context assembly + token truncation
│   │   ├── summarizer.py        # Automatic context summarisation
│   │   ├── ollama_client.py     # Ollama wrapper (streaming + structured)
│   │   ├── storage.py           # JSON file I/O
│   │   ├── validation.py        # Input validation
│   │   ├── variables.py         # {{user}} / {{char}} substitution
│   │   ├── seed.py              # Sample data seeding
│   │   ├── silly_tavern.py      # SillyTavern character card import
│   │   ├── llm_logger.py        # LLM request/response logging
│   │   ├── logging_config.py    # Logging setup
│   │   └── routes/
│   │       ├── chat.py          # POST /api/chat/turn
│   │       ├── characters.py    # /api/characters CRUD
│   │       ├── scenarios.py     # /api/scenarios CRUD
│   │       ├── saves.py         # /api/saves CRUD
│   │       ├── health.py        # /api/health
│   │       └── debug.py         # /api/debug/* (prompt inspection)
│   ├── data/                    # Local saves, characters, scenarios (gitignored)
│   ├── scripts/
│   │   └── seed_long_history.py # Test data generator
│   └── tests/
│       ├── unit/
│       ├── integration/
│       └── resilience/
└── frontend/
    └── src/
        ├── pages/
        │   ├── MainMenu.tsx
        │   ├── NewGame.tsx
        │   ├── Game.tsx
        │   ├── EditCharacters.tsx
        │   └── EditScenarios.tsx
        ├── components/
        ├── hooks/useStream.ts   # Server-Sent Events streaming
        ├── api/client.ts
        └── types/index.ts
```
