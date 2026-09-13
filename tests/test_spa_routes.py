"""Production SPA shell routing tests."""

from collections import Counter

import pytest

from surgite import api

CURRENT_UI_PATHS = ("/password-reset", "/summaries", "/admin")
EXISTING_UI_PATHS = ("/login", "/signup", "/s/example")


@pytest.mark.parametrize("path", CURRENT_UI_PATHS + EXISTING_UI_PATHS)
def test_ui_paths_serve_the_built_spa_shell(client, monkeypatch, tmp_path, path):
    shell = tmp_path / "200.html"
    shell.write_text("<html><body>spa shell</body></html>")
    monkeypatch.setattr(api, "_FRONTEND_BUILD", tmp_path)

    response = client.get(path)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "spa shell" in response.text


@pytest.mark.parametrize("path", CURRENT_UI_PATHS + EXISTING_UI_PATHS)
def test_ui_paths_404_when_the_frontend_build_is_unavailable(client, monkeypatch, tmp_path, path):
    monkeypatch.setattr(api, "_FRONTEND_BUILD", tmp_path)

    response = client.get(path)

    assert response.status_code == 404
    assert response.json() == {"detail": "Frontend build not available"}


def test_spa_shell_get_routes_do_not_shadow_api_get_routes():
    counts = Counter(
        route.path for route in api.app.routes if "GET" in getattr(route, "methods", set())
    )

    for path in api._SPA_SHELL_PATHS:
        assert counts[path] == 1


def test_admin_users_remains_the_json_api(client):
    response = client.get("/admin/users")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert set(response.json()) == {"total", "users"}
