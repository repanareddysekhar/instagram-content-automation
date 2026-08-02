import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx

from app.config import Settings


logger = logging.getLogger("tech_content_agent.telegram")


class TelegramApproval:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.base_url = f"https://api.telegram.org/bot{settings.telegram_bot_token}"

    async def send_for_approval(self, post: dict[str, Any]) -> dict[str, Any]:
        if self.settings.mock_mode:
            return {"sent": False, "reason": "Telegram is disabled in mock mode"}
        if not self.settings.telegram_ready:
            return {"sent": False, "reason": "Telegram is not configured"}

        logger.info("telegram.approval.send.start post_id=%s assets=%d", post["id"], len(post["assets"]))
        async with httpx.AsyncClient(timeout=60) as client:
            is_reel = len(post["assets"]) == 1 and post["assets"][0].endswith(".mp4")
            if is_reel:
                asset = Path(post["assets"][0])
                preview = await client.post(
                    f"{self.base_url}/sendVideo",
                    data={"chat_id": self.settings.telegram_chat_id, "caption": post["hook"]},
                    files={"video": (asset.name, asset.read_bytes(), "video/mp4")},
                )
                preview.raise_for_status()
            else:
                files = {}
                media = []
                for index, asset in enumerate(post["assets"]):
                    key = f"slide{index}"
                    files[key] = (Path(asset).name, Path(asset).read_bytes(), "image/jpeg")
                    media.append(
                        {
                            "type": "photo",
                            "media": f"attach://{key}",
                            "caption": post["hook"] if index == 0 else "",
                        }
                    )
                preview = await client.post(
                    f"{self.base_url}/sendMediaGroup",
                    data={"chat_id": self.settings.telegram_chat_id, "media": json.dumps(media)},
                    files=files,
                )
                preview.raise_for_status()
            decision = await client.post(
                f"{self.base_url}/sendMessage",
                json={
                    "chat_id": self.settings.telegram_chat_id,
                    "text": (
                        f"Review post #{post['id']}\n\n{post['title']}\n\n"
                        f"Fact score: {post['fact_score']:.0%} · "
                        f"Duplicate score: {post['duplicate_score']:.0%}"
                    ),
                    "reply_markup": {
                        "inline_keyboard": [
                            [
                                {"text": "✅ Approve", "callback_data": f"approve:{post['id']}"},
                                {"text": "❌ Reject", "callback_data": f"reject:{post['id']}"},
                            ]
                        ]
                    },
                },
            )
            decision.raise_for_status()
            logger.info(
                "telegram.approval.send.completed post_id=%s message_id=%s",
                post["id"],
                decision.json()["result"].get("message_id"),
            )
            return {"sent": True, "message": decision.json()["result"]}

    async def answer_callback(self, callback_id: str, text: str) -> None:
        if self.settings.mock_mode or not self.settings.telegram_ready:
            return
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                f"{self.base_url}/answerCallbackQuery",
                json={"callback_query_id": callback_id, "text": text},
            )
            response.raise_for_status()
        logger.info("telegram.callback.answered callback_id=%s", callback_id)

    async def get_updates(
        self,
        client: httpx.AsyncClient,
        offset: int | None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "timeout": self.settings.telegram_poll_timeout_seconds,
            "allowed_updates": json.dumps(["callback_query"]),
        }
        if offset is not None:
            params["offset"] = offset
        response = await client.get(f"{self.base_url}/getUpdates", params=params)
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok"):
            raise RuntimeError("Telegram returned an unsuccessful getUpdates response")
        return payload.get("result", [])

    async def poll_updates(
        self,
        handler: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        offset: int | None = None
        timeout = self.settings.telegram_poll_timeout_seconds + 10
        logger.info(
            "telegram.poll.start timeout_seconds=%d",
            self.settings.telegram_poll_timeout_seconds,
        )
        async with httpx.AsyncClient(timeout=timeout) as client:
            while True:
                try:
                    updates = await self.get_updates(client, offset)
                except asyncio.CancelledError:
                    logger.info("telegram.poll.stop")
                    raise
                except Exception as exc:
                    response = getattr(exc, "response", None)
                    logger.error(
                        "telegram.poll.failed error_type=%s status_code=%s",
                        type(exc).__name__,
                        getattr(response, "status_code", None),
                    )
                    await asyncio.sleep(3)
                    continue

                if updates:
                    logger.info("telegram.poll.received updates=%d", len(updates))
                for update in updates:
                    update_id = update.get("update_id")
                    if isinstance(update_id, int):
                        offset = max(offset or 0, update_id + 1)
                    try:
                        await handler(update)
                    except Exception as exc:
                        logger.error(
                            "telegram.update.failed update_id=%s error_type=%s",
                            update_id,
                            type(exc).__name__,
                        )
