import asyncio
from typing import Any

import httpx

from app.config import Settings


class InstagramPublisher:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.root = (
            f"{settings.meta_graph_base_url.rstrip('/')}/"
            f"{settings.meta_graph_api_version.strip('/')}"
        )

    async def publish_carousel(self, post: dict[str, Any]) -> str:
        if not self.settings.instagram_ready:
            raise RuntimeError("Instagram API is not fully configured")

        image_urls = [
            f"{self.settings.app_base_url.rstrip('/')}/generated/{asset.rsplit('/', 1)[-1]}"
            for asset in post["assets"]
        ]
        async with httpx.AsyncClient(timeout=60) as client:
            child_ids = []
            for image_url in image_urls:
                child = await client.post(
                    f"{self.root}/{self.settings.instagram_user_id}/media",
                    data={
                        "image_url": image_url,
                        "is_carousel_item": "true",
                        "access_token": self.settings.instagram_access_token,
                    },
                )
                child.raise_for_status()
                child_ids.append(child.json()["id"])

            container = await client.post(
                f"{self.root}/{self.settings.instagram_user_id}/media",
                data={
                    "media_type": "CAROUSEL",
                    "children": ",".join(child_ids),
                    "caption": f"{post['caption']}\n\n{' '.join(post['hashtags'])}",
                    "access_token": self.settings.instagram_access_token,
                },
            )
            container.raise_for_status()
            container_id = container.json()["id"]

            await self._wait_until_ready(client, container_id)
            published = await client.post(
                f"{self.root}/{self.settings.instagram_user_id}/media_publish",
                data={
                    "creation_id": container_id,
                    "access_token": self.settings.instagram_access_token,
                },
            )
            published.raise_for_status()
            return str(published.json()["id"])

    async def _wait_until_ready(self, client: httpx.AsyncClient, container_id: str) -> None:
        for _ in range(12):
            response = await client.get(
                f"{self.root}/{container_id}",
                params={
                    "fields": "status_code",
                    "access_token": self.settings.instagram_access_token,
                },
            )
            response.raise_for_status()
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
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(
                f"{self.root}/{media_id}/insights",
                params={
                    "metric": self.settings.instagram_insight_metrics,
                    "access_token": self.settings.instagram_access_token,
                },
            )
            response.raise_for_status()
        values: dict[str, float] = {}
        for item in response.json().get("data", []):
            raw = item.get("values", [{}])[0].get("value", 0)
            values[item["name"]] = float(raw)
        return values

