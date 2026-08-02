import httpx
import pytest

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
