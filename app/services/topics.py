import json
import logging
import math
import re
from datetime import UTC, datetime
from pathlib import Path

import feedparser

from app.schema import Topic


logger = logging.getLogger("tech_content_agent.topics")


DEMO_TOPIC = Topic(
    title="Why smaller AI models are becoming the default for production workflows",
    url="https://example.com/demo/smaller-ai-models",
    summary=(
        "Teams are routing extraction, classification, and repetitive automation to "
        "smaller models, reserving frontier models for complex decisions."
    ),
    source_name="Demo: Vendor Engineering Brief",
    published_at=datetime.now(UTC).isoformat(),
    tags=["ai", "engineering", "cost"],
    score=87,
)


class TopicFinder:
    def __init__(self, sources_path: Path):
        self.sources = json.loads(sources_path.read_text())

    def fetch(self, per_source: int = 8) -> list[Topic]:
        logger.info(
            "topics.scan.start sources=%d per_source=%d",
            len(self.sources),
            per_source,
        )
        topics: list[Topic] = []
        for source in self.sources:
            logger.info("topics.source.start name=%s url=%s", source["name"], source["url"])
            feed = feedparser.parse(source["url"])
            if getattr(feed, "bozo", False):
                logger.warning(
                    "topics.source.warning name=%s error_type=%s",
                    source["name"],
                    type(getattr(feed, "bozo_exception", None)).__name__,
                )
            accepted = 0
            for entry in feed.entries[:per_source]:
                title = self._clean(entry.get("title", ""))
                summary = self._clean(entry.get("summary", ""))
                if not title or not entry.get("link"):
                    continue
                topics.append(
                    Topic(
                        title=title,
                        url=entry["link"],
                        summary=summary[:1500],
                        source_name=source["name"],
                        published_at=entry.get("published", ""),
                        tags=source.get("tags", []),
                        score=self._score(title, summary, source.get("trust_tier", 2)),
                    )
                )
                accepted += 1
            logger.info(
                "topics.source.completed name=%s entries=%d accepted=%d",
                source["name"],
                len(feed.entries),
                accepted,
            )
        ranked = sorted(topics, key=lambda topic: topic.score, reverse=True)
        logger.info("topics.scan.completed topics=%d", len(ranked))
        return ranked

    @staticmethod
    def _clean(value: str) -> str:
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", value)).strip()

    @staticmethod
    def _score(title: str, summary: str, trust_tier: int) -> float:
        recency_proxy = min(len(summary) / 50, 20)
        signal_terms = (
            "launch",
            "release",
            "research",
            "security",
            "open source",
            "developer",
            "model",
            "api",
        )
        signal = sum(6 for term in signal_terms if term in f"{title} {summary}".lower())
        trust = max(0, 40 - (trust_tier - 1) * 10)
        return round(min(100, trust + recency_proxy + signal + math.log2(len(title) + 1)), 2)
