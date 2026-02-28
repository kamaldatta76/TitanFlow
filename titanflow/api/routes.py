"""TitanFlow API Routes — health checks, status, module control, personality hot-reload."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from titanflow.personality import PersonalityStore

router = APIRouter(prefix="/api", tags=["titanflow"])


def get_engine():
    """Dependency injection for the engine. Set at startup."""
    from titanflow.main import _engine
    return _engine


def require_api_key(request: Request, engine=Depends(get_engine)):
    """Verify X-API-Key header for protected routes."""
    configured_key = engine.config.api_key
    if not configured_key:
        return  # No key configured = auth disabled (dev mode)
    provided = request.headers.get("X-API-Key", "")
    if provided != configured_key:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "titanflow"}


@router.get("/status")
async def status(engine=Depends(get_engine), _=Depends(require_api_key)) -> dict[str, Any]:
    return engine.status()


@router.get("/modules")
async def modules(engine=Depends(get_engine), _=Depends(require_api_key)) -> dict[str, Any]:
    return {
        name: {
            "enabled": m.enabled,
            "description": m.description,
        }
        for name, m in engine.modules.items()
    }


@router.get("/llm/health")
async def llm_health(engine=Depends(get_engine), _=Depends(require_api_key)) -> dict[str, Any]:
    return await engine.llm.health_check()


@router.get("/jobs")
async def scheduled_jobs(engine=Depends(get_engine), _=Depends(require_api_key)) -> list[dict[str, Any]]:
    return engine.scheduler.list_jobs()


# ─── Personality Hot-Reload ────────────────────────────────────────────────────

@router.get("/personality")
async def get_personality(engine=Depends(get_engine), _=Depends(require_api_key)) -> dict[str, Any]:
    """Return current in-memory personality config for this instance."""
    return {
        "instance": engine.config.name,
        "personality": PersonalityStore.get(engine.config.name),
    }


@router.post("/personality")
async def set_personality(request: Request, engine=Depends(get_engine), _=Depends(require_api_key)) -> dict[str, Any]:
    """Hot-reload personality config pushed from TitanPortal (no restart required).

    Accepts the same JSON payload that TitanPortal sends:
      { slider_silly, slider_chatty, slider_hyper, slider_voices,
        temperature, top_p, preset, model, context_window,
        response_length, memory_enabled, plugins }
    """
    body = await request.json()
    PersonalityStore.set(engine.config.name, body)
    return {"status": "ok", "instance": engine.config.name, "applied": body}
