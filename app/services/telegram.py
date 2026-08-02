import json
from pathlib import Path
from typing import Any

import httpx

from app.config import Settings


class TelegramApproval:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.base_url = f"https://api.telegram.org/bot{settings.telegram_bot_token}"

    async def send_for_approval(self, post: dict[str, Any]) -> dict[str, Any]:
        if self.settings.mock_mode:
            return {"sent": False, "reason": "Telegram is disabled in mock mode"}
        if not self.settings.telegram_ready:
            return {"sent": False, "reason": "Telegram is not configured"}

        async with httpx.AsyncClient(timeout=60) as client:
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
            album = await client.post(
                f"{self.base_url}/sendMediaGroup",
                data={"chat_id": self.settings.telegram_chat_id, "media": json.dumps(media)},
                files=files,
            )
            album.raise_for_status()
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
