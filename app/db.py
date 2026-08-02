import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator


SCHEMA = """
CREATE TABLE IF NOT EXISTS topics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    url TEXT NOT NULL UNIQUE,
    summary TEXT NOT NULL DEFAULT '',
    source_name TEXT NOT NULL,
    tags_json TEXT NOT NULL DEFAULT '[]',
    score REAL NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'new',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_id INTEGER NOT NULL,
    status TEXT NOT NULL,
    title TEXT NOT NULL,
    hook TEXT NOT NULL,
    caption TEXT NOT NULL,
    hashtags_json TEXT NOT NULL,
    slides_json TEXT NOT NULL,
    claims_json TEXT NOT NULL,
    assets_json TEXT NOT NULL DEFAULT '[]',
    duplicate_score REAL NOT NULL DEFAULT 0,
    fact_score REAL NOT NULL DEFAULT 0,
    instagram_media_id TEXT,
    rejection_note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(topic_id) REFERENCES topics(id)
);

CREATE TABLE IF NOT EXISTS metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id INTEGER NOT NULL,
    metric TEXT NOT NULL,
    value REAL NOT NULL,
    measured_at TEXT NOT NULL,
    UNIQUE(post_id, metric, measured_at),
    FOREIGN KEY(post_id) REFERENCES posts(id)
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id INTEGER,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
"""


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def init(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def upsert_topic(self, topic: dict[str, Any]) -> int:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO topics
                    (title, url, summary, source_name, tags_json, score, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(url) DO UPDATE SET
                    title=excluded.title,
                    summary=excluded.summary,
                    score=MAX(topics.score, excluded.score)
                """,
                (
                    topic["title"],
                    topic["url"],
                    topic.get("summary", ""),
                    topic["source_name"],
                    json.dumps(topic.get("tags", [])),
                    topic.get("score", 0),
                    now_iso(),
                ),
            )
            row = conn.execute("SELECT id FROM topics WHERE url = ?", (topic["url"],)).fetchone()
            return int(row["id"])

    def create_post(self, topic_id: int, draft: dict[str, Any], status: str) -> int:
        stamp = now_iso()
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO posts (
                    topic_id, status, title, hook, caption, hashtags_json,
                    slides_json, claims_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    topic_id,
                    status,
                    draft["title"],
                    draft["hook"],
                    draft["caption"],
                    json.dumps(draft["hashtags"]),
                    json.dumps(draft["slides"]),
                    json.dumps(draft["claims"]),
                    stamp,
                    stamp,
                ),
            )
            return int(cursor.lastrowid)

    def update_post(self, post_id: int, **fields: Any) -> None:
        allowed = {
            "status",
            "assets_json",
            "claims_json",
            "duplicate_score",
            "fact_score",
            "instagram_media_id",
            "rejection_note",
        }
        safe = {key: value for key, value in fields.items() if key in allowed}
        if not safe:
            return
        safe["updated_at"] = now_iso()
        assignments = ", ".join(f"{key} = ?" for key in safe)
        with self.connect() as conn:
            conn.execute(
                f"UPDATE posts SET {assignments} WHERE id = ?",
                (*safe.values(), post_id),
            )

    def get_post(self, post_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT posts.*, topics.url AS topic_url, topics.source_name,
                       topics.summary AS topic_summary, topics.tags_json AS topic_tags_json
                FROM posts JOIN topics ON topics.id = posts.topic_id
                WHERE posts.id = ?
                """,
                (post_id,),
            ).fetchone()
        return self._decode_post(row) if row else None

    def list_posts(self, limit: int = 30) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT posts.*, topics.url AS topic_url, topics.source_name,
                       topics.summary AS topic_summary, topics.tags_json AS topic_tags_json
                FROM posts JOIN topics ON topics.id = posts.topic_id
                ORDER BY posts.created_at DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [self._decode_post(row) for row in rows]

    def list_topics(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM topics ORDER BY score DESC, created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {
                **dict(row),
                "tags": json.loads(row["tags_json"]),
            }
            for row in rows
        ]

    def get_topic_by_url(self, url: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM topics WHERE url = ?", (url,)).fetchone()
        if not row:
            return None
        return {
            **dict(row),
            "tags": json.loads(row["tags_json"]),
        }

    def add_event(
        self, event_type: str, post_id: int | None = None, payload: dict[str, Any] | None = None
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO events (post_id, event_type, payload_json, created_at) VALUES (?, ?, ?, ?)",
                (post_id, event_type, json.dumps(payload or {}), now_iso()),
            )

    def list_events(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM events ORDER BY created_at DESC, id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {
                "id": int(row["id"]),
                "post_id": row["post_id"],
                "event_type": row["event_type"],
                "payload": json.loads(row["payload_json"] or "{}"),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def save_metrics(self, post_id: int, values: dict[str, float]) -> None:
        stamp = now_iso()
        with self.connect() as conn:
            for metric, value in values.items():
                conn.execute(
                    "INSERT OR IGNORE INTO metrics (post_id, metric, value, measured_at) VALUES (?, ?, ?, ?)",
                    (post_id, metric, value, stamp),
                )

    def latest_metrics(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT metrics.post_id, posts.title, metrics.metric, metrics.value,
                       MAX(metrics.measured_at) AS measured_at
                FROM metrics JOIN posts ON posts.id = metrics.post_id
                GROUP BY metrics.post_id, metrics.metric
                ORDER BY measured_at DESC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def tag_performance(self) -> dict[str, float]:
        """Return normalized engagement quality by tag for future topic ranking."""
        with self.connect() as conn:
            posts = conn.execute(
                """
                SELECT posts.id, topics.tags_json
                FROM posts JOIN topics ON topics.id = posts.topic_id
                WHERE posts.status = 'published'
                """
            ).fetchall()
            metric_rows = conn.execute(
                """
                SELECT m.post_id, m.metric, m.value
                FROM metrics m
                JOIN (
                    SELECT post_id, metric, MAX(measured_at) AS measured_at
                    FROM metrics GROUP BY post_id, metric
                ) latest
                  ON latest.post_id = m.post_id
                 AND latest.metric = m.metric
                 AND latest.measured_at = m.measured_at
                """
            ).fetchall()
        by_post: dict[int, dict[str, float]] = {}
        for row in metric_rows:
            by_post.setdefault(int(row["post_id"]), {})[str(row["metric"])] = float(row["value"])
        tag_scores: dict[str, list[float]] = {}
        for post in posts:
            values = by_post.get(int(post["id"]), {})
            views = max(values.get("views", values.get("reach", 0)), 1)
            quality = (
                values.get("likes", 0)
                + values.get("comments", 0) * 2
                + values.get("saves", 0) * 4
                + values.get("shares", 0) * 5
            ) / views
            for tag in json.loads(post["tags_json"]):
                tag_scores.setdefault(tag, []).append(quality)
        return {
            tag: round(sum(scores) / len(scores), 4)
            for tag, scores in tag_scores.items()
        }

    def historical_titles(self) -> list[str]:
        with self.connect() as conn:
            rows = conn.execute("SELECT title FROM posts").fetchall()
        return [str(row["title"]) for row in rows]

    def dashboard_counts(self) -> dict[str, int]:
        with self.connect() as conn:
            statuses = conn.execute(
                "SELECT status, COUNT(*) AS count FROM posts GROUP BY status"
            ).fetchall()
            topics = conn.execute("SELECT COUNT(*) AS count FROM topics").fetchone()
        counts = {row["status"]: row["count"] for row in statuses}
        counts["topics"] = int(topics["count"])
        counts["posts"] = sum(row["count"] for row in statuses)
        return counts

    @staticmethod
    def _decode_post(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        for key, output_key in (
            ("hashtags_json", "hashtags"),
            ("slides_json", "slides"),
            ("claims_json", "claims"),
            ("assets_json", "assets"),
            ("topic_tags_json", "topic_tags"),
        ):
            item[output_key] = json.loads(item.pop(key, "[]") or "[]")
        return item
