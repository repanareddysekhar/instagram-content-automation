from pathlib import Path

import pytest

from app.config import Settings
from app.db import Database
from app.pipeline import ContentPipeline


@pytest.mark.asyncio
async def test_demo_pipeline_approval_publish_and_learning(tmp_path: Path) -> None:
    settings = Settings(
        mock_mode=True,
        database_path=str(tmp_path / "agent.db"),
        app_base_url="http://localhost:8000",
    )
    settings.generated_dir.mkdir(parents=True, exist_ok=True)
    db = Database(settings.database_file)
    db.init()
    pipeline = ContentPipeline(settings, db)
    pipeline.renderer.output_dir = tmp_path / "generated"
    pipeline.renderer.output_dir.mkdir()

    post = await pipeline.run(force_demo=True)
    assert post["status"] == "pending_approval"
    assert len(post["assets"]) == 6
    assert post["fact_score"] == 1

    published = await pipeline.approve(post["id"])
    assert published["status"] == "published"
    assert published["instagram_media_id"].startswith("mock-instagram-")

    metrics = await pipeline.sync_metrics()
    assert metrics["posts_synced"] == 1
    assert "ai" in metrics["learned_tag_scores"]


@pytest.mark.asyncio
async def test_second_identical_draft_is_blocked_as_duplicate(tmp_path: Path) -> None:
    settings = Settings(mock_mode=True, database_path=str(tmp_path / "agent.db"))
    db = Database(settings.database_file)
    db.init()
    pipeline = ContentPipeline(settings, db)
    pipeline.renderer.output_dir = tmp_path / "generated"
    pipeline.renderer.output_dir.mkdir()

    first = await pipeline.run(force_demo=True)
    assert first["status"] == "pending_approval"
    second = await pipeline.run(force_demo=True)
    assert second["status"] == "blocked_duplicate"

