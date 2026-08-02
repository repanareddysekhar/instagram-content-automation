import json

import httpx
import pytest

from app.config import Settings
from app.services.telegram import TelegramApproval


@pytest.mark.asyncio
async def test_get_updates_requests_callback_queries_with_offset() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["offset"] == "101"
        assert request.url.params["timeout"] == "7"
        assert json.loads(request.url.params["allowed_updates"]) == ["callback_query"]
        return httpx.Response(
            200,
            json={
                "ok": True,
                "result": [
                    {
                        "update_id": 101,
                        "callback_query": {"id": "callback", "data": "approve:3"},
                    }
                ],
            },
        )

    service = TelegramApproval(
        Settings(
            _env_file=None,
            telegram_bot_token="bot-token",
            telegram_chat_id="chat-id",
            telegram_poll_timeout_seconds=7,
        )
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        updates = await service.get_updates(client, offset=101)

    assert updates[0]["callback_query"]["data"] == "approve:3"


def test_telegram_auto_mode_polls_only_in_development() -> None:
    development = Settings(_env_file=None, app_env="development")
    production = Settings(_env_file=None, app_env="production")

    assert development.telegram_polling_enabled
    assert not production.telegram_polling_enabled
