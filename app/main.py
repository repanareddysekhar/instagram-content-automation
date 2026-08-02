import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.db import Database
from app.pipeline import ContentPipeline
from app.schema import DecisionRequest, PipelineRequest


settings = get_settings()
db = Database(settings.database_file)
pipeline = ContentPipeline(settings, db)
STATIC_DIR = Path(__file__).with_name("static")


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init()
    settings.generated_dir.mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(
    title="Tech Content Agent",
    version="0.1.0",
    description="Source-grounded content production with approval and official Instagram publishing.",
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/generated", StaticFiles(directory=settings.generated_dir), name="generated")


def admin_guard(authorization: str | None) -> None:
    if settings.app_env == "development" and settings.admin_token == "change-me":
        return
    expected = f"Bearer {settings.admin_token}"
    if not authorization or not secrets.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail="Invalid admin token")


def to_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, LookupError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=502, detail=str(exc))


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "mode": "mock" if settings.mock_mode else "live",
        "integrations": {
            "text_ai": {
                "provider": settings.text_provider,
                "ready": settings.text_provider_ready,
            },
            "image_ai": {
                "provider": settings.image_provider,
                "enabled": settings.enable_ai_art,
                "ready": settings.image_provider_ready,
            },
            "providers": {
                "openai": settings.openai_ready,
                "gemini": settings.gemini_ready,
                "anthropic": settings.anthropic_ready,
                "openai_compatible": settings.openai_compatible_ready,
            },
            "telegram": settings.telegram_ready,
            "instagram": settings.instagram_ready,
        },
    }


@app.get("/api/dashboard")
async def dashboard(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    admin_guard(authorization)
    return {
        "counts": db.dashboard_counts(),
        "posts": db.list_posts(),
        "topics": db.list_topics(),
        "metrics": db.latest_metrics(),
        "learned_tag_scores": db.tag_performance(),
        "mode": "mock" if settings.mock_mode else "live",
    }


@app.post("/api/pipeline/run")
async def run_pipeline(
    payload: PipelineRequest,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    admin_guard(authorization)
    try:
        return await pipeline.run(
            topic_url=str(payload.topic_url) if payload.topic_url else None,
            force_demo=payload.force_demo,
        )
    except Exception as exc:
        raise to_http_error(exc) from exc


@app.post("/api/posts/{post_id}/approve")
async def approve(
    post_id: int,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    admin_guard(authorization)
    try:
        return await pipeline.approve(post_id)
    except Exception as exc:
        raise to_http_error(exc) from exc


@app.post("/api/posts/{post_id}/reject")
async def reject(
    post_id: int,
    payload: DecisionRequest,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    admin_guard(authorization)
    try:
        return pipeline.reject(post_id, payload.note)
    except Exception as exc:
        raise to_http_error(exc) from exc


@app.post("/api/posts/{post_id}/publish")
async def publish(
    post_id: int,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    admin_guard(authorization)
    try:
        return await pipeline.publish(post_id)
    except Exception as exc:
        raise to_http_error(exc) from exc


@app.post("/api/metrics/sync")
async def sync_metrics(
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    admin_guard(authorization)
    try:
        return await pipeline.sync_metrics()
    except Exception as exc:
        raise to_http_error(exc) from exc


@app.post("/webhooks/telegram")
async def telegram_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> dict[str, bool]:
    if settings.telegram_webhook_secret and not secrets.compare_digest(
        x_telegram_bot_api_secret_token or "",
        settings.telegram_webhook_secret,
    ):
        raise HTTPException(status_code=401, detail="Invalid Telegram webhook secret")

    update = await request.json()
    callback = update.get("callback_query")
    if not callback:
        return {"ok": True}
    data = callback.get("data", "")
    try:
        action, raw_post_id = data.split(":", 1)
        post_id = int(raw_post_id)
    except (ValueError, AttributeError):
        raise HTTPException(status_code=400, detail="Invalid callback payload")

    if action == "approve":
        background_tasks.add_task(pipeline.approve, post_id)
        await pipeline.telegram.answer_callback(callback["id"], "Approved — publishing started")
    elif action == "reject":
        pipeline.reject(post_id, "Rejected in Telegram")
        await pipeline.telegram.answer_callback(callback["id"], "Rejected")
    else:
        raise HTTPException(status_code=400, detail="Unknown approval action")
    return {"ok": True}
