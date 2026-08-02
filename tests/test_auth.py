from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app import main


def request_from(host: str) -> SimpleNamespace:
    return SimpleNamespace(client=SimpleNamespace(host=host))


def test_development_allows_loopback_without_token(monkeypatch) -> None:
    monkeypatch.setattr(main.settings, "app_env", "development")
    monkeypatch.setattr(main.settings, "admin_token", "secret")

    main.admin_guard(request_from("127.0.0.1"), None)  # type: ignore[arg-type]


def test_development_still_protects_remote_requests(monkeypatch) -> None:
    monkeypatch.setattr(main.settings, "app_env", "development")
    monkeypatch.setattr(main.settings, "admin_token", "secret")

    with pytest.raises(HTTPException) as exc:
        main.admin_guard(request_from("192.0.2.10"), None)  # type: ignore[arg-type]
    assert exc.value.status_code == 401


def test_production_requires_token_even_on_loopback(monkeypatch) -> None:
    monkeypatch.setattr(main.settings, "app_env", "production")
    monkeypatch.setattr(main.settings, "admin_token", "secret")
    request = request_from("127.0.0.1")

    with pytest.raises(HTTPException):
        main.admin_guard(request, None)  # type: ignore[arg-type]
    main.admin_guard(request, "Bearer secret")  # type: ignore[arg-type]
