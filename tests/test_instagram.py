import httpx
import pytest
from urllib.parse import parse_qs

from app.config import Settings
from app.services.instagram import InstagramPublisher


@pytest.mark.asyncio
async def test_insights_keeps_access_token_out_of_url(monkeypatch) -> None:
    token = "secret-instagram-token"

    def handler(request: httpx.Request) -> httpx.Response:
        assert token not in str(request.url)
        assert request.headers["authorization"] == f"Bearer {token}"
        return httpx.Response(
            200,
            json={"data": [{"name": "views", "values": [{"value": 42}]}]},
        )

    publisher = InstagramPublisher(
        Settings(
            _env_file=None,
            mock_mode=False,
            instagram_user_id="user-id",
            instagram_access_token=token,
            meta_graph_api_version="v26.0",
        )
    )
    monkeypatch.setattr(
        publisher,
        "_client",
        lambda timeout: httpx.AsyncClient(
            headers={"Authorization": f"Bearer {token}"},
            transport=httpx.MockTransport(handler),
        ),
    )

    assert await publisher.insights("real-media-id") == {"views": 42.0}


def test_instagram_api_error_includes_graph_message() -> None:
    response = httpx.Response(
        400,
        request=httpx.Request("POST", "https://graph.instagram.com/v26.0/user/media"),
        json={
            "error": {
                "message": "The image URL is not publicly accessible",
                "code": 100,
                "error_subcode": 2207005,
            }
        },
    )

    with pytest.raises(RuntimeError, match="image URL is not publicly accessible"):
        InstagramPublisher._raise_api_error(response, "carousel item creation")


def test_post_description_adds_hashtags_and_disclaimer() -> None:
    publisher = InstagramPublisher(
        Settings(
            _env_file=None,
            post_disclaimer="Verify before acting.",
        )
    )

    description = publisher._post_description(
        {"caption": "A useful engineering lesson.", "hashtags": ["#Engineering", "#AI"]}
    )

    assert description == (
        "A useful engineering lesson.\n\n#Engineering #AI\n\n"
        "Disclaimer: Verify before acting."
    )


@pytest.mark.asyncio
async def test_publish_reel_uses_public_video_and_description(monkeypatch) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST" and request.url.path.endswith("/media"):
            return httpx.Response(200, json={"id": "container-id"})
        if request.method == "GET" and request.url.path.endswith("/container-id"):
            return httpx.Response(200, json={"status_code": "FINISHED"})
        if request.method == "POST" and request.url.path.endswith("/media_publish"):
            return httpx.Response(200, json={"id": "published-reel-id"})
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    publisher = InstagramPublisher(
        Settings(
            _env_file=None,
            mock_mode=False,
            app_base_url="https://example.ngrok.app",
            instagram_user_id="user-id",
            instagram_access_token="secret-token",
            meta_graph_api_version="v26.0",
            post_disclaimer="Verify before acting.",
        )
    )
    monkeypatch.setattr(
        publisher,
        "_client",
        lambda timeout: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    media_id = await publisher.publish_reel(
        {
            "id": 7,
            "caption": "A useful lesson.",
            "hashtags": ["#Engineering"],
        },
        "data/generated/post-7-reel.mp4",
    )

    assert media_id == "published-reel-id"
    creation_payload = parse_qs(requests[0].content.decode())
    assert creation_payload["media_type"] == ["REELS"]
    assert creation_payload["video_url"] == [
        "https://example.ngrok.app/generated/post-7-reel.mp4"
    ]
    assert "Disclaimer: Verify before acting." in creation_payload["caption"][0]
