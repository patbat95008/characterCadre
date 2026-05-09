import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.logging_config import setup_logging

setup_logging()
logger = logging.getLogger(__name__)

from app import seed, storage  # noqa: E402
from app.ollama_client import OLLAMA_BASE_URL, OLLAMA_MODEL  # noqa: E402
from app.routes.characters import router as characters_router  # noqa: E402
from app.routes.chat import router as chat_router  # noqa: E402
from app.routes.debug import router as debug_router  # noqa: E402
from app.routes.health import router as health_router  # noqa: E402
from app.routes.saves import router as saves_router  # noqa: E402
from app.routes.scenarios import router as scenarios_router  # noqa: E402

app = FastAPI(title="CharacterCadre", version="0.3.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(characters_router, prefix="/api")
app.include_router(scenarios_router, prefix="/api")
app.include_router(saves_router, prefix="/api")
app.include_router(chat_router, prefix="/api")
app.include_router(debug_router, prefix="/api")
app.include_router(health_router, prefix="/api")


@app.on_event("startup")
async def on_startup():
    port = int(os.environ.get("PORT", "8000"))
    storage._ensure_dirs()
    seed.run_if_empty()
    logger.info(
        "CharacterCadre starting — model=%s url=%s port=%d "
        "data_dir=%s characters_dir=%s scenarios_dir=%s saves_dir=%s avatars_dir=%s",
        OLLAMA_MODEL,
        OLLAMA_BASE_URL,
        port,
        storage.DATA_DIR,
        storage.CHARACTERS_DIR,
        storage.SCENARIOS_DIR,
        storage.SAVES_DIR,
        storage.AVATARS_DIR,
    )


# Mount avatars as a static directory at /avatars/. Done after startup so
# AVATARS_DIR is guaranteed to exist (seed.run_if_empty() also creates it).
storage._ensure_dirs()
app.mount("/avatars", StaticFiles(directory=str(storage.AVATARS_DIR)), name="avatars")
