import json
import logging
from pathlib import Path
from time import perf_counter
from typing import Any

from app.config import Settings
from app.db import Database
from app.schema import Topic
from app.services.ai import ContentWriter
from app.services.assets import CarouselRenderer
from app.services.instagram import InstagramPublisher
from app.services.quality import highest_duplicate_score, verify_claims
from app.services.telegram import TelegramApproval
from app.services.topics import DEMO_TOPIC, TopicFinder


logger = logging.getLogger("tech_content_agent.pipeline")


class ContentPipeline:
    def __init__(self, settings: Settings, db: Database):
        self.settings = settings
        self.db = db
        self.finder = TopicFinder(Path(__file__).with_name("sources.json"))
        self.writer = ContentWriter(settings)
        self.renderer = CarouselRenderer(settings)
        self.telegram = TelegramApproval(settings)
        self.instagram = InstagramPublisher(settings)

    def discover(self, force_demo: bool = False) -> list[dict[str, Any]]:
        mode = "demo" if force_demo or self.settings.mock_mode else "live"
        logger.info("pipeline.discovery.start mode=%s", mode)
        topics = [DEMO_TOPIC] if force_demo or self.settings.mock_mode else self.finder.fetch()
        learned = self.db.tag_performance()
        results = []
        for topic in topics:
            item = topic.model_dump()
            # A strong save/share rate can add up to 15 points, while source trust
            # and freshness remain the dominant signals.
            learned_lift = max((learned.get(tag, 0) for tag in topic.tags), default=0)
            item["score"] = round(min(100, item["score"] + min(15, learned_lift * 50)), 2)
            topic_id = self.db.upsert_topic(item)
            results.append({"id": topic_id, **item})
        self.db.add_event("topics.discovered", payload={"count": len(results)})
        logger.info("pipeline.discovery.completed mode=%s topics=%d", mode, len(results))
        return results

    async def run(self, topic_url: str | None = None, force_demo: bool = False) -> dict[str, Any]:
        logger.info(
            "pipeline.run.start requested_topic=%s force_demo=%s",
            topic_url or "top-ranked",
            force_demo,
        )
        if topic_url:
            selected = self.db.get_topic_by_url(topic_url)
            if not selected:
                topics = self.discover(force_demo=force_demo)
                selected = next((topic for topic in topics if topic["url"] == topic_url), None)
            if not selected:
                raise ValueError("Requested topic URL was not found in the trusted-source batch")
        else:
            topics = self.discover(force_demo=force_demo)
            selected = topics[0] if topics else None
        if not selected:
            raise RuntimeError("No eligible topics were discovered")

        logger.info(
            "pipeline.topic.selected topic_id=%s source=%s score=%s url=%s",
            selected["id"],
            selected["source_name"],
            selected["score"],
            selected["url"],
        )
        topic = Topic.model_validate(
            {
                key: value
                for key, value in selected.items()
                if key not in {"id", "tags_json", "status", "created_at"}
            }
        )
        generation_started = perf_counter()
        self.db.add_event(
            "generation.started",
            payload={
                "provider": self.settings.text_provider,
                "topic_id": selected["id"],
                "topic_title": topic.title,
            },
        )
        try:
            draft = self.writer.write(topic)
        except Exception as exc:
            self.db.add_event(
                "generation.failed",
                payload={
                    "provider": self.settings.text_provider,
                    "topic_id": selected["id"],
                    "error_type": type(exc).__name__,
                },
            )
            raise
        generation_ms = round((perf_counter() - generation_started) * 1000)
        self.db.add_event(
            "generation.completed",
            payload={
                "provider": self.settings.text_provider,
                "topic_id": selected["id"],
                "duration_ms": generation_ms,
                "slides": len(draft.slides),
                "claims": len(draft.claims),
            },
        )
        logger.info(
            "pipeline.generation.completed topic_id=%s duration_ms=%d title=%s",
            selected["id"],
            generation_ms,
            draft.title,
        )
        duplicate_score = highest_duplicate_score(
            draft.title,
            self.db.historical_titles(),
        )
        verified_claims, fact_score = verify_claims(
            [claim.model_dump() for claim in draft.claims],
            [topic.url],
        )
        draft.claims = [type(draft.claims[0]).model_validate(claim) for claim in verified_claims]

        status = "blocked_duplicate" if duplicate_score >= 0.72 else "rendering"
        if fact_score < 1:
            status = "blocked_facts"
        post_id = self.db.create_post(selected["id"], draft.model_dump(), status)
        self.db.update_post(
            post_id,
            duplicate_score=duplicate_score,
            fact_score=fact_score,
            claims_json=json.dumps(verified_claims),
        )
        self.db.add_event("post.drafted", post_id, {"status": status})
        logger.info(
            "pipeline.quality.completed post_id=%d duplicate_score=%.3f fact_score=%.3f status=%s",
            post_id,
            duplicate_score,
            fact_score,
            status,
        )

        if status.startswith("blocked"):
            self.db.add_event(
                "post.blocked",
                post_id,
                {
                    "reason": status,
                    "duplicate_score": duplicate_score,
                    "fact_score": fact_score,
                },
            )
            logger.warning("pipeline.run.blocked post_id=%d reason=%s", post_id, status)
            return self.db.get_post(post_id) or {}

        assets = self.renderer.render(
            post_id,
            [slide.model_dump() for slide in draft.slides],
            topic.source_name,
        )
        target_status = "approved" if self.settings.auto_publish else "pending_approval"
        self.db.update_post(
            post_id,
            assets_json=json.dumps(assets),
            status=target_status,
        )
        self.db.add_event(
            "assets.rendered",
            post_id,
            {
                "count": len(assets),
                "provider": (
                    self.settings.image_provider
                    if self.renderer.image_provider
                    else "deterministic"
                ),
            },
        )
        post = self.db.get_post(post_id) or {}

        if self.settings.auto_publish:
            return await self.publish(post_id)

        telegram_result = await self.telegram.send_for_approval(post)
        self.db.add_event("approval.requested", post_id, telegram_result)
        logger.info(
            "pipeline.run.completed post_id=%d status=%s assets=%d telegram_sent=%s",
            post_id,
            target_status,
            len(assets),
            telegram_result.get("sent", False),
        )
        return self.db.get_post(post_id) or {}

    async def approve(self, post_id: int) -> dict[str, Any]:
        post = self._require_post(post_id)
        if post["status"] not in {"pending_approval", "approved", "publish_failed"}:
            raise ValueError(f"Post cannot be approved from status {post['status']}")
        self.db.update_post(post_id, status="approved")
        self.db.add_event("post.approved", post_id)
        if self.settings.publish_after_approval and (
            self.settings.instagram_ready or self.settings.mock_mode
        ):
            return await self.publish(post_id)
        return self._require_post(post_id)

    def reject(self, post_id: int, note: str = "") -> dict[str, Any]:
        post = self._require_post(post_id)
        if post["status"] not in {"pending_approval", "approved"}:
            raise ValueError(f"Post cannot be rejected from status {post['status']}")
        self.db.update_post(post_id, status="rejected", rejection_note=note)
        self.db.add_event("post.rejected", post_id, {"note": note})
        return self._require_post(post_id)

    async def publish(self, post_id: int) -> dict[str, Any]:
        post = self._require_post(post_id)
        if post["status"] not in {"approved", "publish_failed"}:
            raise ValueError(f"Post cannot be published from status {post['status']}")
        self.db.update_post(post_id, status="publishing")
        try:
            if self.settings.mock_mode:
                media_id = f"mock-instagram-{post_id}"
            else:
                media_id = await self.instagram.publish_carousel(post)
            self.db.update_post(
                post_id,
                status="published",
                instagram_media_id=media_id,
            )
            self.db.add_event("post.published", post_id, {"instagram_media_id": media_id})
        except Exception as exc:
            self.db.update_post(post_id, status="publish_failed")
            self.db.add_event("post.publish_failed", post_id, {"error": str(exc)})
            raise
        return self._require_post(post_id)

    async def sync_metrics(self) -> dict[str, Any]:
        published = [
            post for post in self.db.list_posts(limit=100)
            if post["status"] == "published" and post["instagram_media_id"]
        ]
        logger.info("metrics.sync.start eligible_posts=%d", len(published))
        synced = 0
        skipped = 0
        failed = 0
        for post in published:
            if self.settings.mock_mode:
                values = {
                    "views": 1000 + post["id"] * 137,
                    "reach": 780 + post["id"] * 91,
                    "likes": 62 + post["id"] * 4,
                    "saves": 18 + post["id"],
                    "shares": 9 + post["id"],
                }
            else:
                if str(post["instagram_media_id"]).startswith("mock-instagram-"):
                    skipped += 1
                    self.db.add_event(
                        "metrics.skipped",
                        post["id"],
                        {"reason": "mock_media_id_in_live_mode"},
                    )
                    logger.warning(
                        "metrics.post.skipped post_id=%d reason=mock_media_id_in_live_mode",
                        post["id"],
                    )
                    continue
                try:
                    values = await self.instagram.insights(post["instagram_media_id"])
                except Exception as exc:
                    failed += 1
                    response = getattr(exc, "response", None)
                    status_code = getattr(response, "status_code", None)
                    self.db.add_event(
                        "metrics.failed",
                        post["id"],
                        {
                            "error_type": type(exc).__name__,
                            "status_code": status_code,
                        },
                    )
                    logger.error(
                        "metrics.post.failed post_id=%d error_type=%s status_code=%s",
                        post["id"],
                        type(exc).__name__,
                        status_code,
                    )
                    continue
            self.db.save_metrics(post["id"], values)
            synced += 1
            logger.info(
                "metrics.post.completed post_id=%d metrics=%d",
                post["id"],
                len(values),
            )
        self.db.add_event(
            "metrics.synced",
            payload={"posts": synced, "skipped": skipped, "failed": failed},
        )
        logger.info(
            "metrics.sync.completed synced=%d skipped=%d failed=%d",
            synced,
            skipped,
            failed,
        )
        return {
            "posts_synced": synced,
            "posts_skipped": skipped,
            "posts_failed": failed,
            "metrics": self.db.latest_metrics(),
            "learned_tag_scores": self.db.tag_performance(),
        }

    def _require_post(self, post_id: int) -> dict[str, Any]:
        post = self.db.get_post(post_id)
        if not post:
            raise LookupError("Post not found")
        return post
