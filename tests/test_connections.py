import httpx
import pytest

from surgite.auth import ensure_bootstrap_user
from surgite.connections import _encrypt_secret, connection_credentials
from surgite.db import GitConnectionRow, RepoRow, get_session
from surgite.secrets import decrypt

FORGE = {"name": "forge", "origin": "https://git.example.test", "username": "nick", "token": "x"}
PRIVATE_REPO = "https://git.example.test/team/private.git"


def test_token_connection_is_encrypted_and_can_be_assigned(client, monkeypatch):
    monkeypatch.setattr("surgite.api._submit_ingest", lambda *args: None)
    created = client.post("/connections", json={**FORGE, "token": "secret-token"})
    assert created.status_code == 201
    connection = created.json()
    assert connection["status"] == "connected"
    assert "secret-token" not in created.text
    with get_session() as session:
        row = session.get(GitConnectionRow, connection["id"])
        assert row and row.encrypted_secret and "secret-token" not in row.encrypted_secret
        assert decrypt(row.encrypted_secret)[-14:] == 'secret-token"}'
    repo = client.post("/repos", json={"url": PRIVATE_REPO, "connection_id": connection["id"]})
    assert repo.status_code == 201
    assert repo.json()["connection_id"] == connection["id"]


def test_disconnect_keeps_repo_and_marks_connection_required(client, monkeypatch):
    monkeypatch.setattr("surgite.api._submit_ingest", lambda *args: None)
    connection = client.post("/connections", json=FORGE).json()
    repo = client.post(
        "/repos", json={"url": PRIVATE_REPO, "connection_id": connection["id"]}
    ).json()
    assert client.delete(f"/connections/{connection['id']}").status_code == 204
    listed = client.get("/connections").json()
    assert listed[0]["status"] == "disconnected"
    assert listed[0]["affected_repositories"] == 1
    assert client.post(f"/repos/{repo['id']}/ingest").status_code == 409
    with get_session() as session:
        assert session.get(RepoRow, repo["id"]).connection_id == connection["id"]
        assert session.get(GitConnectionRow, connection["id"]).encrypted_secret is None


def test_reusing_a_disconnected_token_connection_reconnects_it(client):
    connection = client.post("/connections", json=FORGE).json()
    assert client.delete(f"/connections/{connection['id']}").status_code == 204
    reconnected = client.post("/connections", json={**FORGE, "token": "new"})
    assert reconnected.status_code == 201
    assert reconnected.json()["id"] == connection["id"]
    assert reconnected.json()["status"] == "connected"


def test_rejects_embedded_repository_credentials(client, monkeypatch):
    monkeypatch.setattr("surgite.api._submit_ingest", lambda *args: None)
    response = client.post(
        "/repos", json={"url": "https://nick:secret@git.example.test/team/private.git"}
    )
    assert response.status_code == 400


def test_github_refresh_rotates_then_disconnects_on_failure(monkeypatch):
    calls = []

    def post(url, **kwargs):
        calls.append(kwargs["data"])
        tokens = {"access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 3600}
        return httpx.Response(200, json=tokens, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", post)
    expired = {"access_token": "old", "refresh_token": "r1", "expires_at": "2000-01-01T00:00:00"}
    with get_session() as session:
        row = GitConnectionRow(
            owner_id=ensure_bootstrap_user(session).id,
            name="GitHub",
            kind="github",
            host="github.com",
            encrypted_secret=_encrypt_secret(expired),
        )
        session.add(row)
        session.commit()
        assert connection_credentials(row, session).token == "new-access"
        assert connection_credentials(row, session).token == "new-access"
        assert [call["refresh_token"] for call in calls] == ["r1"]

        def fail(url, **kwargs):
            raise httpx.ConnectError("down")

        monkeypatch.setattr(httpx, "post", fail)
        row.encrypted_secret = _encrypt_secret(expired)
        session.commit()
        with pytest.raises(RuntimeError, match="requires reconnection"):
            connection_credentials(row, session)
        assert row.disconnected_at is not None and row.encrypted_secret is None
