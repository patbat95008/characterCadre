import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.ollama_client import OLLAMA_MODEL, _get_client

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/health/ollama")
async def ollama_health():
    """Return 200 if the configured model is loaded in Ollama, 503 otherwise."""
    client = _get_client()
    try:
        ps = await client.ps()
        loaded = any(m.model.startswith(OLLAMA_MODEL) for m in ps.models)
        if loaded:
            return {"status": "ok", "model": OLLAMA_MODEL}
        return JSONResponse(
            status_code=503,
            content={"status": "model_not_loaded", "model": OLLAMA_MODEL},
        )
    except Exception as exc:
        logger.debug("Ollama health check failed: %s", exc)
        return JSONResponse(status_code=503, content={"status": "unreachable"})
