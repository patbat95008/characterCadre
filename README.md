# CharacterCadre

Browser-based local RPG roleplay app powered by Ollama. Features a multi-phase game loop with a Director AI, DM narration, companion characters, and player option drafting.

## Prerequisites

- Python 3.11+
- Node 18+
- [Ollama](https://ollama.com/) installed and running

## Backend Setup

```bash
cd backend
python -m venv .venv

# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate

pip install -e ".[dev]"
pip freeze > requirements.lock
```

## Frontend Setup

```bash
cd frontend
npm install
```

## Pull a Model

```bash
ollama pull mistral-small3.1
```

## Running

Open two terminals:

```bash
# Terminal 1 — backend
cd backend
.venv\Scripts\activate
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
| `LOG_LEVEL` | `INFO` | Logging level (DEBUG, INFO, WARNING, ERROR) |
| `DATA_DIR` | `data` | Path to data directory (relative to backend/) |
| `PORT` | `8000` | Port (used in startup banner only; uvicorn sets the actual port) |

Set via shell before starting the backend, e.g.:
```bash
set LOG_LEVEL=DEBUG   # Windows
export LOG_LEVEL=DEBUG  # macOS/Linux
```

## Running Tests

```bash
cd backend
pytest tests/unit/ -v
```

## Project Structure

```
charactercadre/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI entrypoint
│   │   ├── logging_config.py    # Logging setup
│   │   ├── ollama_client.py     # Ollama wrapper (streaming + structured)
│   │   ├── prompt_builder.py    # Context assembly + token truncation
│   │   ├── models.py            # Pydantic domain models
│   │   ├── storage.py           # JSON file I/O
│   │   ├── variables.py         # {{user}} / {{char}} substitution
│   │   ├── fixtures.py          # Hardcoded Stage 1 scenario and characters
│   │   └── routes/
│   │       ├── debug.py         # /api/debug/* (prompt inspection)
│   │       └── turn.py          # /api/chat/turn (Stage 2 multi-phase loop)
│   ├── data/
│   │   └── saves/               # stage1.json (gitignored)
│   └── tests/
│       └── unit/
├── frontend/
│   └── src/
│       ├── components/Chat.tsx
│       ├── hooks/useStream.ts
│       ├── api/client.ts
│       └── types/index.ts
└── README.md
```
