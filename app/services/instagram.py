import asyncio
import logging
from pathlib import Path
from typing import Any

import httpx

from app.config import Settings


logger = logging.getLogger("tech_content_agent.instagram")


class InstagramPublisher:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.root = (
            f"{settings.meta_graph_base_url.rstrip('/')}/"
            f"{settings.meta_graph_api_version.strip('/')}"
        )

    def _client(self, timeout: float) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=timeout,
            headers={"Authorization": f"Bearer {self.settings.instagram_access_token}"},
        )

    @staticmethod
    def _raise_api_error(response: httpx.Response, operation: str) -> None:
        if not response.is_error:
            return
        try:
            error = response.json().get("error", {})
        except (ValueError, AttributeError):
            error = {}
        message = str(error.get("message") or response.reason_phrase or "Unknown error")
        code = error.get("code")
        subcode = error.get("error_subcode")
        logger.error(
            "instagram.api.failed operation=%s status_code=%d code=%s subcode=%s message=%s",
            operation,
            response.status_code,
            code,
            subcode,
            message,
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(
                f"Instagram {operation} failed ({response.status_code}): {message}"
            ) from exc

    async def publish_carousel(self, post: dict[str, Any]) -> str:
        if not self.settings.instagram_ready:
            raise RuntimeError("Instagram API is not fully configured")

        image_urls = [
            f"{self.settings.app_base_url.rstrip('/')}/generated/{asset.rsplit('/', 1)[-1]}"
            for asset in post["assets"]
        ]
        logger.info("instagram.publish.start post_id=%s assets=%d", post["id"], len(image_urls))
        async with self._client(timeout=60) as client:
            child_ids = []
            for image_url in image_urls:
                child = await client.post(
                    f"{self.root}/{self.settings.instagram_user_id}/media",
                    data={
                        "image_url": image_url,
                        "is_carousel_item": "true",
                    },
                )
                self._raise_api_error(child, "carousel item creation")
                child_ids.append(child.json()["id"])

            container = await client.post(
                f"{self.root}/{self.settings.instagram_user_id}/media",
                data={
                    "media_type": "CAROUSEL",
                    "children": ",".join(child_ids),
                    "caption": self._post_description(post),
                },
            )
            self._raise_api_error(container, "carousel creation")
            container_id = container.json()["id"]

            await self._wait_until_ready(client, container_id)
            published = await client.post(
                f"{self.root}/{self.settings.instagram_user_id}/media_publish",
                data={
                    "creation_id": container_id,
                },
            )
            self._raise_api_error(published, "carousel publishing")
            media_id = str(published.json()["id"])
            logger.info("instagram.publish.completed post_id=%s media_id=%s", post["id"], media_id)
            return media_id

    async def publish_reel(self, post: dict[str, Any], video_asset: str) -> str:
        if not self.settings.instagram_ready:
            raise RuntimeError("Instagram API is not fully configured")
        video_url = (
            f"{self.settings.app_base_url.rstrip('/')}/generated/"
            f"{Path(video_asset).name}"
        )
        logger.info("instagram.reel.publish.start post_id=%s video_url=%s", post["id"], video_url)
        async with self._client(timeout=120) as client:
            container = await client.post(
                f"{self.root}/{self.settings.instagram_user_id}/media",
                data={
                    "media_type": "REELS",
                    "video_url": video_url,
                    "caption": self._post_description(post),
                    "share_to_feed": "true",
                },
            )
            self._raise_api_error(container, "reel creation")
            container_id = container.json()["id"]
            await self._wait_until_ready(client, container_id)
            published = await client.post(
                f"{self.root}/{self.settings.instagram_user_id}/media_publish",
                data={"creation_id": container_id},
            )
            self._raise_api_error(published, "reel publishing")
            media_id = str(published.json()["id"])
            logger.info("instagram.reel.publish.completed post_id=%s media_id=%s", post["id"], media_id)
            return media_id

    def _post_description(self, post: dict[str, Any]) -> str:
        parts = [post["caption"].strip(), " ".join(post["hashtags"]).strip()]
        if self.settings.post_disclaimer.strip():
            parts.append(f"Disclaimer: {self.settings.post_disclaimer.strip()}")
        return "\n\n".join(part for part in parts if part)

    async def _wait_until_ready(self, client: httpx.AsyncClient, container_id: str) -> None:
        for _ in range(12):
            response = await client.get(
                f"{self.root}/{container_id}",
                params={
                    "fields": "status_code",
                },
            )
            self._raise_api_error(response, "container status check")
            status = response.json().get("status_code")
            if status == "FINISHED":
                return
            if status in {"ERROR", "EXPIRED"}:
                raise RuntimeError(f"Instagram container failed with status {status}")
            await asyncio.sleep(5)
        raise TimeoutError("Instagram media container was not ready within 60 seconds")

    async def insights(self, media_id: str) -> dict[str, float]:
        if not self.settings.instagram_ready:
            raise RuntimeError("Instagram API is not fully configured")
        logger.info("instagram.insights.start media_id=%s", media_id)
        async with self._client(timeout=30) as client:
            response = await client.get(
                f"{self.root}/{media_id}/insights",
                params={
                    "metric": self.settings.instagram_insight_metrics,
                },
            )
            self._raise_api_error(response, "insights sync")
        values: dict[str, float] = {}
        for item in response.json().get("data", []):
            raw = item.get("values", [{}])[0].get("value", 0)
            values[item["name"]] = float(raw)
        logger.info("instagram.insights.completed media_id=%s metrics=%d", media_id, len(values))
        return values
