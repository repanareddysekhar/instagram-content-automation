import base64
import io
import json

import httpx
import pytest
from PIL import Image

from app.config import Settings
from app.services.ai import (
    AnthropicTextProvider,
    ContentWriter,
    GeminiTextProvider,
    OpenAICompatibleTextProvider,
)
from app.services.assets import GeminiImageProvider
from app.services.assets import CarouselRenderer
from app.schema import Topic


def _draft_json() -> str:
    return json.dumps(
        {
            "title": "A grounded title",
            "hook": "A useful hook",
            "caption": "A concise caption ending in a question?",
            "hashtags": ["#AI", "#Engineering", "#Tech"],
            "format": "carousel",
            "slides": [
                {"headline": f"Slide {index}", "body": "Body", "visual_prompt": "Shapes"}
                for index in range(1, 7)
            ],
            "claims": [
                {
                    "text": "A claim",
                    "source_url": "https://example.com/source",
                    "evidence": "Evidence",
                    "verified": False,
                }
            ],
        }
    )


def test_gemini_uses_native_structured_output() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-goog-api-key"] == "gemini-key"
        assert request.url.path.endswith("/models/gemini-test:generateContent")
        payload = json.loads(request.content)
        assert payload["generationConfig"]["responseMimeType"] == "application/json"
        assert payload["generationConfig"]["responseJsonSchema"]["type"] == "object"
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {"content": {"parts": [{"text": _draft_json()}]}}
                ]
            },
        )

    provider = GeminiTextProvider(
        "gemini-key",
        "gemini-test",
        "https://gemini.test/v1beta",
        5,
        httpx.Client(transport=httpx.MockTransport(handler)),
    )
    result = json.loads(provider.generate("prompt", {"type": "object"}))
    assert result["title"] == "A grounded title"


def test_anthropic_uses_native_structured_output() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-api-key"] == "anthropic-key"
        payload = json.loads(request.content)
        assert payload["output_config"]["format"]["type"] == "json_schema"
        assert payload["output_config"]["format"]["schema"]["type"] == "object"
        return httpx.Response(200, json={"content": [{"type": "text", "text": _draft_json()}]})

    provider = AnthropicTextProvider(
        "anthropic-key",
        "claude-test",
        "https://anthropic.test/v1",
        5,
        httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert json.loads(provider.generate("prompt", {"type": "object"}))["format"] == "carousel"


def test_openai_compatible_provider_supports_keyless_local_service() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "authorization" not in request.headers
        payload = json.loads(request.content)
        assert payload["model"] == "local-model"
        assert payload["response_format"]["type"] == "json_schema"
        assert "provider" not in payload
        assert "plugins" not in payload
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": _draft_json()}}]},
        )

    provider = OpenAICompatibleTextProvider(
        "",
        "local-model",
        "http://localhost:11434/v1",
        5,
        httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert json.loads(provider.generate("prompt", {"type": "object"}))["hook"] == "A useful hook"


def test_openrouter_requires_schema_capable_provider_and_response_healing() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["provider"] == {"require_parameters": True}
        assert payload["plugins"] == [{"id": "response-healing"}]
        return httpx.Response(
            200,
            json={
                "model": "test/free-model",
                "usage": {"prompt_tokens": 10, "completion_tokens": 20},
                "choices": [{"message": {"content": _draft_json()}}],
            },
        )

    provider = OpenAICompatibleTextProvider(
        "openrouter-key",
        "openrouter/free",
        "https://openrouter.ai/api/v1",
        5,
        httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert json.loads(provider.generate("prompt", {"type": "object"}))["format"] == "carousel"


def test_writer_retries_schema_invalid_response() -> None:
    class FlakyProvider:
        calls = 0

        def generate(self, prompt, schema):
            self.calls += 1
            return "{}" if self.calls == 1 else _draft_json()

    writer = ContentWriter(
        Settings(
            _env_file=None,
            mock_mode=False,
            text_provider="openai_compatible",
            openai_compatible_base_url="http://localhost:11434/v1",
            openai_compatible_text_model="local-model",
            ai_generation_attempts=2,
        )
    )
    flaky = FlakyProvider()
    writer.provider = flaky
    draft = writer.write(
        Topic(
            title="Test topic",
            url="https://example.com/topic",
            summary="Summary",
            source_name="Example",
        )
    )

    assert flaky.calls == 2
    assert draft.title == "A grounded title"


def test_gemini_image_provider_decodes_inline_image() -> None:
    buffer = io.BytesIO()
    Image.new("RGB", (4, 5), "red").save(buffer, "PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode()

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["generationConfig"]["responseModalities"] == ["IMAGE"]
        assert payload["generationConfig"]["responseFormat"]["image"]["aspectRatio"] == "4:5"
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {"content": {"parts": [{"inlineData": {"data": encoded}}]}}
                ]
            },
        )

    provider = GeminiImageProvider(
        "gemini-key",
        "gemini-image-test",
        "https://gemini.test/v1",
        5,
        httpx.Client(transport=httpx.MockTransport(handler)),
    )
    image = provider.generate("prompt")
    assert image is not None
    assert image.size == (4, 5)


def test_provider_readiness_does_not_require_openai() -> None:
    gemini = Settings(
        mock_mode=False,
        text_provider="gemini",
        gemini_api_key="key",
        openai_api_key="",
    )
    anthropic = Settings(
        mock_mode=False,
        text_provider="anthropic",
        anthropic_api_key="key",
        openai_api_key="",
    )
    local = Settings(
        mock_mode=False,
        text_provider="openai_compatible",
        openai_compatible_base_url="http://localhost:11434/v1",
        openai_compatible_text_model="qwen",
    )

    assert gemini.text_provider_ready
    assert anthropic.text_provider_ready
    assert local.text_provider_ready
    assert not gemini.openai_ready


def test_live_mode_rejects_missing_selected_provider_key() -> None:
    from app.services.ai import ContentWriter

    writer = ContentWriter(
        Settings(mock_mode=False, text_provider="gemini", gemini_api_key="")
    )
    with pytest.raises(RuntimeError, match="TEXT_PROVIDER=gemini"):
        writer.write(None)  # type: ignore[arg-type]


def test_mock_mode_never_enables_paid_image_generation(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    renderer = CarouselRenderer(
        Settings(
            mock_mode=True,
            enable_ai_art=True,
            image_provider="gemini",
            gemini_api_key="key",
        )
    )
    assert renderer.image_provider is None
