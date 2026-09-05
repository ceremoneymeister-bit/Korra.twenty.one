"""Имя и роль мастера доходят до движка в одном HTTP-запросе."""

from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_DASHBOARD_SESSION_TOKEN", "identity-test-token")
    from korra_cli import web_server

    with TestClient(web_server.app, raise_server_exceptions=False) as client:
        client.headers["Authorization"] = "Bearer identity-test-token"
        yield client


def test_create_stores_identity_and_engine_reads_role(client):
    from agent.prompt_builder import load_soul_md
    from korra_cli import profiles

    soul = "Ты — Секретарь. Помогай готовить встречи и повестки.\n"
    response = client.post("/api/profiles", json={
        "name": "Secretary", "display_name": " Секретарь ",
        "soul": soul, "description": "Готовит встречи", "no_skills": True,
    })

    assert response.status_code == 200, response.text
    assert response.json()["name"] == "secretary"
    home = Path(response.json()["path"])
    assert (home / "SOUL.md").read_text() == soul
    assert soul.strip() in load_soul_md(home_override=home)
    meta = profiles.read_profile_meta(home)
    assert meta["display_name"] == "Секретарь"
    assert meta["description"] == "Готовит встречи"
    listed = client.get("/api/profiles").json()["profiles"]
    assert next(p for p in listed if p["name"] == "secretary")["display_name"] == "Секретарь"


def test_omitted_identity_preserves_clone_and_explicit_role_overrides_it(client):
    soul = "Ты — наставник. Задавай по одному вопросу."
    source = client.post("/api/profiles", json={
        "name": "mentor", "display_name": "Наставник", "soul": soul,
        "no_skills": True,
    })
    assert source.status_code == 200, source.text
    for name, fields, expected in (
        ("copy", {}, soul),
        ("seller", {"soul": "Ты помогаешь продавать."}, "Ты помогаешь продавать."),
        ("blank", {"soul": ""}, ""),
    ):
        result = client.post("/api/profiles", json={
            "name": name, "clone_from": "mentor", **fields,
        })
        assert result.status_code == 200, result.text
        assert client.get(f"/api/profiles/{name}/soul").json()["content"] == expected
    assert client.get("/api/profiles/mentor/soul").json()["content"] == soul


def test_invalid_display_name_does_not_leave_a_profile(client):
    from korra_cli.profiles import get_profile_dir

    response = client.post("/api/profiles", json={
        "name": "invalid", "display_name": "А" * 65, "no_skills": True,
    })
    assert response.status_code == 400
    assert not get_profile_dir("invalid").exists()


@pytest.mark.parametrize("failed_file", ["SOUL.md", "profile.yaml"])
def test_identity_write_failure_allows_retry(client, monkeypatch, failed_file):
    import os
    from korra_cli.profiles import get_profile_dir

    replace = os.replace

    def fail_identity(source, target, *args, **kwargs):
        if Path(target).name == failed_file:
            raise OSError("Не удалось записать инструкции")
        return replace(source, target, *args, **kwargs)

    body = {"name": "retry", "display_name": "Помощник", "soul": "Точная роль", "no_skills": True}
    with monkeypatch.context() as scoped:
        scoped.setattr(os, "replace", fail_identity)
        response = client.post("/api/profiles", json=body)
    assert response.status_code == 500
    assert not get_profile_dir("retry").exists()
    retry = client.post("/api/profiles", json=body)
    assert retry.status_code == 200, retry.text
    assert client.get("/api/profiles/retry/soul").json()["content"] == body["soul"]


def test_duplicate_create_cannot_change_existing_identity(client):
    body = {"name": "existing", "soul": "Сохранить мою роль", "no_skills": True}
    assert client.post("/api/profiles", json=body).status_code == 200
    duplicate = client.post("/api/profiles", json={**body, "soul": "Чужая роль"})
    assert duplicate.status_code == 400
    assert client.get("/api/profiles/existing/soul").json()["content"] == body["soul"]
