from pathlib import Path

import pytest

from app.config import Settings
from app.db import Database
from app.pipeline import ContentPipeline


@pytest.mark.asyncio
async def test_demo_pipeline_approval_publish_and_learning(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        mock_mode=True,
        publish_after_approval=True,
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


@pytest.mark.asyncio
async def test_live_metrics_skip_historical_mock_media_ids(tmp_path: Path) -> None:
    database_path = str(tmp_path / "agent.db")
    mock_settings = Settings(
        _env_file=None,
        mock_mode=True,
        database_path=database_path,
    )
    db = Database(mock_settings.database_file)
    db.init()
    mock_pipeline = ContentPipeline(mock_settings, db)
    mock_pipeline.renderer.output_dir = tmp_path / "generated"
    mock_pipeline.renderer.output_dir.mkdir()

    post = await mock_pipeline.run(force_demo=True)
    published = await mock_pipeline.approve(post["id"])
    assert published["instagram_media_id"].startswith("mock-instagram-")

    live_settings = Settings(
        _env_file=None,
        mock_mode=False,
        database_path=database_path,
        text_provider="openai_compatible",
        openai_compatible_base_url="http://localhost:11434/v1",
        openai_compatible_text_model="test-model",
    )
    result = await ContentPipeline(live_settings, db).sync_metrics()

    assert result["posts_synced"] == 0
    assert result["posts_skipped"] == 1
    assert result["posts_failed"] == 0


@pytest.mark.asyncio
async def test_approval_can_be_tested_without_publishing(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        mock_mode=False,
        publish_after_approval=False,
        database_path=str(tmp_path / "agent.db"),
        instagram_user_id="instagram-user",
        instagram_access_token="instagram-token",
        meta_graph_api_version="v26.0",
    )
    db = Database(settings.database_file)
    db.init()
    topic_id = db.upsert_topic(
        {
            "title": "Approval-only topic",
            "url": "https://example.com/approval-only",
            "summary": "A test topic",
            "source_name": "Test source",
            "tags": ["test"],
            "score": 1,
        }
    )
    post_id = db.create_post(
        topic_id=topic_id,
        draft={
            "title": "Approval-only test",
            "hook": "Test hook",
            "caption": "Test caption",
            "slides": [],
            "claims": [],
            "hashtags": [],
        },
        status="pending_approval",
    )

    approved = await ContentPipeline(settings, db).approve(post_id)

    assert approved["status"] == "approved"
