from pathlib import Path

from app.config import Settings
from app.services.assets import ReelRenderer


def test_reel_renderer_creates_video_and_vertical_frames(tmp_path: Path, monkeypatch) -> None:
    settings = Settings(
        _env_file=None,
        enable_reel_voiceover=False,
        reel_segment_seconds=1.0,
        reel_transition_seconds=0.2,
    )
    renderer = ReelRenderer(settings)
    renderer.output_dir = tmp_path

    def fake_run(command, check, capture_output, text):
        assert command[0].endswith("ffmpeg")
        assert "-filter_complex" in command
        Path(command[-1]).write_bytes(b"video")

    monkeypatch.setattr("app.services.assets.subprocess.run", fake_run)

    assets = renderer.render(
        post_id=7,
        slides=[
            {
                "headline": "First insight",
                "body": "A concise and readable explanation.",
                "visual_prompt": "Unused for deterministic reel rendering.",
            },
            {
                "headline": "Second insight",
                "body": "A second useful explanation.",
                "visual_prompt": "Unused for deterministic reel rendering.",
            },
        ],
        source_name="Example",
        hook="This is the hook that stops the scroll",
    )

    assert assets == [str(tmp_path / "post-7-reel.mp4")]
    assert (tmp_path / "post-7-reel.mp4").read_bytes() == b"video"
    assert (tmp_path / "post-7-reel-frame-1.jpg").is_file()
    assert (tmp_path / "post-7-reel-frame-4.jpg").is_file()


def test_opening_hook_removes_source_annotation_and_limits_length() -> None:
    hook = (
        "Dependabot automates dependency updates, but its default settings can overwhelm "
        "a repo with pull requests. (Source: github.blog/example)"
    )

    result = ReelRenderer._opening_hook(hook)

    assert "Source:" not in result
    assert len(result.split()) <= 16


def test_fallback_voiceover_matches_the_visible_cards() -> None:
    script = ReelRenderer._fallback_voiceover(
        "A concise hook",
        [
            {
                "headline": "Insight",
                "body": "Useful explanation.",
            }
        ],
    )

    assert script == (
        "A concise hook Insight. Useful explanation. "
        "Follow for practical, source-grounded tech signals."
    )
