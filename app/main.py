import asyncio
import secrets
from contextlib import asynccontextmanager, suppress
from ipaddress import ip_address
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.db import Database
from app.logging_config import configure_logging
from app.pipeline import ContentPipeline
from app.schema import DecisionRequest, PipelineRequest


settings = get_settings()
logger = configure_logging(settings.log_level, settings.log_file)
db = Database(settings.database_file)
pipeline = ContentPipeline(settings, db)
STATIC_DIR = Path(__file__).with_name("static")
telegram_action_tasks: set[asyncio.Task[Any]] = set()


@asynccontextmanager
async def lifespan(_: FastAPI):
    logger.info(
        "app.start env=%s mode=%s text_provider=%s text_ready=%s image_provider=%s",
        settings.app_env,
        "mock" if settings.mock_mode else "live",
        settings.text_provider,
        settings.text_provider_ready,
        settings.image_provider,
    )
    db.init()
    settings.generated_dir.mkdir(parents=True, exist_ok=True)
    polling_task: asyncio.Task[None] | None = None
    if settings.telegram_ready and settings.telegram_polling_enabled:
        polling_task = asyncio.create_task(
            pipeline.telegram.poll_updates(process_telegram_update)
        )
        logger.info("telegram.update_mode mode=polling")
    elif settings.telegram_ready:
        logger.info("telegram.update_mode mode=webhook")
    try:
        yield
    finally:
        if polling_task:
            polling_task.cancel()
            with suppress(asyncio.CancelledError):
                await polling_task
        for task in telegram_action_tasks:
            task.cancel()
        logger.info("app.stop")


app = FastAPI(
    title="Tech Content Agent",
    version="0.1.0",
    description="Source-grounded content production with approval and official Instagram publishing.",
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/generated", StaticFiles(directory=settings.generated_dir), name="generated")


def admin_guard(request: Request, authorization: str | None) -> None:
    if settings.app_env == "development" and request.client:
        try:
            if ip_address(request.client.host).is_loopback:
                return
        except ValueError:
            if request.client.host == "localhost":
                return
    expected = f"Bearer {settings.admin_token}"
    if not authorization or not secrets.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail="Invalid admin token")


def redact_secrets(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: redact_secrets(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_secrets(item) for item in value]
    if not isinstance(value, str):
        return value
    redacted = value
    for secret in (
        settings.admin_token,
        settings.openai_api_key,
        settings.gemini_api_key,
        settings.anthropic_api_key,
        settings.openai_compatible_api_key,
        settings.telegram_bot_token,
        settings.instagram_access_token,
    ):
        if secret:
            redacted = redacted.replace(secret, "<redacted>")
    return redacted


def to_http_error(exc: Exception) -> HTTPException:
    detail = redact_secrets(str(exc))
    if isinstance(exc, LookupError):
        return HTTPException(status_code=404, detail=detail)
    if isinstance(exc, ValueError):
        return HTTPException(status_code=409, detail=detail)
    return HTTPException(status_code=502, detail=detail)


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "mode": "mock" if settings.mock_mode else "live",
        "publish_after_approval": settings.publish_after_approval,
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
            "telegram_updates": {
                "mode": (
                    "polling" if settings.telegram_polling_enabled else "webhook"
                ),
                "ready": settings.telegram_ready,
            },
            "instagram": settings.instagram_ready,
        },
    }


@app.get("/api/dashboard")
async def dashboard(
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    admin_guard(request, authorization)
    return {
        "counts": db.dashboard_counts(),
        "posts": db.list_posts(),
        "topics": db.list_topics(),
        "metrics": db.latest_metrics(),
        "learned_tag_scores": db.tag_performance(),
        "events": redact_secrets(db.list_events()),
        "mode": "mock" if settings.mock_mode else "live",
    }


@app.post("/api/topics/discover")
async def discover_topics(
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    admin_guard(request, authorization)
    try:
        topics = pipeline.discover(force_demo=False)
        return {"count": len(topics), "topics": topics}
    except Exception as exc:
        raise to_http_error(exc) from exc


@app.post("/api/pipeline/run")
async def run_pipeline(
    payload: PipelineRequest,
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    admin_guard(request, authorization)
    try:
        return await pipeline.run(
            topic_url=str(payload.topic_url) if payload.topic_url else None,
            force_demo=payload.force_demo,
            content_format=payload.content_format,
        )
    except Exception as exc:
        raise to_http_error(exc) from exc


@app.post("/api/posts/{post_id}/approve")
async def approve(
    post_id: int,
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    admin_guard(request, authorization)
    try:
        return await pipeline.approve(post_id)
    except Exception as exc:
        raise to_http_error(exc) from exc


@app.post("/api/posts/{post_id}/reject")
async def reject(
    post_id: int,
    payload: DecisionRequest,
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    admin_guard(request, authorization)
    try:
        return pipeline.reject(post_id, payload.note)
    except Exception as exc:
        raise to_http_error(exc) from exc


@app.post("/api/posts/{post_id}/publish")
async def publish(
    post_id: int,
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    admin_guard(request, authorization)
    try:
        return await pipeline.publish(post_id)
    except Exception as exc:
        raise to_http_error(exc) from exc


@app.post("/api/metrics/sync")
async def sync_metrics(
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    admin_guard(request, authorization)
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
    try:
        await process_telegram_update(update, background_tasks)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


async def _approve_from_telegram(post_id: int) -> None:
    logger.info("telegram.approval.start post_id=%d", post_id)
    try:
        post = await pipeline.approve(post_id)
    except Exception as exc:
        post = db.get_post(post_id)
        if post and post["status"] == "publish_failed":
            db.add_event(
                "telegram.publish.failed",
                post_id,
                {"error": redact_secrets(str(exc))},
            )
            logger.error(
                "telegram.publish.failed post_id=%d error_type=%s error=%s",
                post_id,
                type(exc).__name__,
                redact_secrets(str(exc)),
            )
            return
        db.add_event(
            "telegram.approval.failed",
            post_id,
            {
                "error_type": type(exc).__name__,
                "error": redact_secrets(str(exc)),
            },
        )
        logger.error(
            "telegram.approval.failed post_id=%d error_type=%s",
            post_id,
            type(exc).__name__,
        )
        return
    db.add_event(
        "telegram.approval.completed",
        post_id,
        {"status": post["status"]},
    )
    logger.info("telegram.approval.completed post_id=%d status=%s", post_id, post["status"])


def _schedule_telegram_approval(post_id: int) -> None:
    task = asyncio.create_task(_approve_from_telegram(post_id))
    telegram_action_tasks.add(task)
    task.add_done_callback(telegram_action_tasks.discard)


async def _answer_telegram_callback(callback_id: str, text: str) -> None:
    try:
        await pipeline.telegram.answer_callback(callback_id, text)
    except Exception as exc:
        logger.warning(
            "telegram.callback.answer_failed callback_id=%s error_type=%s",
            callback_id,
            type(exc).__name__,
        )


async def process_telegram_update(
    update: dict[str, Any],
    background_tasks: BackgroundTasks | None = None,
) -> None:
    callback = update.get("callback_query")
    if not callback:
        return
    data = callback.get("data", "")
    try:
        action, raw_post_id = data.split(":", 1)
        post_id = int(raw_post_id)
    except (ValueError, AttributeError):
        raise ValueError("Invalid callback payload")

    db.add_event(
        "telegram.callback.received",
        post_id,
        {"action": action, "update_id": update.get("update_id")},
    )
    logger.info(
        "telegram.callback.received update_id=%s post_id=%d action=%s",
        update.get("update_id"),
        post_id,
        action,
    )

    if action == "approve":
        db.add_event(
            "telegram.approval.accepted",
            post_id,
            {"update_id": update.get("update_id")},
        )
        if background_tasks:
            background_tasks.add_task(_approve_from_telegram, post_id)
        else:
            _schedule_telegram_approval(post_id)
        await _answer_telegram_callback(callback["id"], "Approved — publishing started")
    elif action == "reject":
        pipeline.reject(post_id, "Rejected in Telegram")
        await _answer_telegram_callback(callback["id"], "Rejected")
    else:
        raise ValueError("Unknown approval action")
