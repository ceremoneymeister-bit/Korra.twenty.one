"""Real HTTP/profile/package integration in a synthetic, offline installation."""

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_DASHBOARD_SESSION_TOKEN", "template-test")
    from korra_cli import profiles, web_server

    registrations = []
    monkeypatch.setattr(profiles, "_maybe_register_gateway_service", registrations.append)
    monkeypatch.setattr(profiles, "create_wrapper_script", lambda name: None)
    # Route/profile integration, not dashboard startup acceptance. Entering
    # TestClient starts lifespan background seed/reconcile/hosted-room jobs,
    # which race fixture writes (including the protected root SOUL.md).
    # Keep the real routes/middleware; isolated image acceptance owns startup.
    http = TestClient(web_server.app, raise_server_exceptions=False)
    try:
        http.headers["Authorization"] = "Bearer template-test"
        yield http, tmp_path, registrations
    finally:
        http.close()


def request_for(http, **overrides):
    template = http.get("/api/agent-templates").json()["templates"][0]
    return {
        "name": "designer", "display_name": "Наш дизайнер",
        "template_id": template["id"], "template_version": template["version"],
        "idempotency_key": "test-operation-0001", **overrides,
    }


def test_catalogue_requires_auth_and_returns_only_public_metadata(client):
    http, _, _ = client
    unauthenticated = http.get("/api/agent-templates", headers={"Authorization": "Bearer wrong"})
    assert unauthenticated.status_code in (401, 403)
    response = http.get("/api/agent-templates")
    assert response.status_code == 200
    for template in response.json()["templates"]:
        assert set(template) == {"id", "version", "name", "description", "requirements"}
        assert template["requirements"]


def test_add_loads_role_and_skills_without_user_state_and_replay_preserves_edits(client):
    http, root, registrations = client
    from agent.prompt_builder import load_soul_md
    from agent.secret_scope import load_env_file
    from korra_cli import profiles
    from korra_constants import set_hermes_home_override, reset_hermes_home_override
    from tools.skills_tool import skills_list, skill_view

    (root / "config.yaml").write_text(yaml.safe_dump({
        "model": {"provider": "custom:chosen", "default": "test-model"},
        "custom_providers": [{"name": "chosen", "base_url": "https://model.invalid/v1", "key_env": "CHOSEN_API_KEY"}],
        # A private/root image route must never leak into the ready agent.
        "image_gen": {"provider": "private-owner-route", "model": "private-model"},
        "gateway": {"multiplex_profiles": True},
    }))
    (root / ".env").write_text("CHOSEN_API_KEY=synthetic-key\nTELEGRAM_BOT_TOKEN=never-copy\n")
    protected = {"SOUL.md": "чужая роль", "memories/USER.md": "личная память", "cron/jobs.json": "[]", "workspace/design.txt": "чужой макет"}
    for rel, text in protected.items():
        file = root / rel
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(text)
    body = request_for(http, provider="custom:chosen", model="test-model")
    response = http.post("/api/profiles", json=body)
    assert response.status_code == 200, response.text
    saved = response.json()
    target = Path(saved["path"])
    assert saved["model_set"] and not saved["generation_checked"]
    assert saved["generation"] == {
        "configured": True,
        "available": saved["generation"]["available"],
        "status": "ready" if saved["generation"]["available"] else "needs_auth",
        "provider": "openai-codex",
        "model": "gpt-image-2.5-sunburst",
        "platforms": ["cli", "api_server"],
        "live_tested": False,
    }
    assert "Дизайнер" in load_soul_md(home_override=target)
    assert (target / "skills/visual-design/SKILL.md").is_file()
    assert list((target / "skills").rglob("powerpoint/SKILL.md"))
    scope = set_hermes_home_override(str(target))
    try:
        available = json.loads(skills_list())
        assert available["success"]
        assert "visual-design" in [skill["name"] for skill in available["skills"]]
        viewed = json.loads(skill_view("visual-design", preprocess=False))
        assert viewed["success"], viewed
    finally:
        reset_hermes_home_override(scope)
    assert profiles.read_profile_meta(target)["display_name"] == "Наш дизайнер"
    assert "TELEGRAM_BOT_TOKEN" not in load_env_file(target / ".env")
    assert load_env_file(target / ".env")["CHOSEN_API_KEY"] == "synthetic-key"
    config = yaml.safe_load((target / "config.yaml").read_text())
    assert config["platforms"]["api_server"]["enabled"] is False
    assert config["image_gen"] == {
        "provider": "openai-codex",
        "model": "gpt-image-2.5-sunburst",
    }
    from korra_cli.tools_config import _get_platform_tools
    assert "image_gen" in _get_platform_tools(config, "cli")
    assert "image_gen" in _get_platform_tools(config, "api_server")
    for rel, text in protected.items():
        assert (root / rel).read_text() == text
        assert not (target / rel).exists() or (target / rel).read_text() != text
    assert registrations == ["designer"]  # never register the staging home
    assert "designer" in [p["name"] for p in http.get("/api/profiles").json()["profiles"]]
    (target / "SOUL.md").write_text("Моя новая роль")
    (target / "skills/visual-design/SKILL.md").write_text("Мои правила")
    replay = http.post("/api/profiles", json=body)
    assert replay.json() == saved
    assert (target / "SOUL.md").read_text() == "Моя новая роль"
    assert (target / "skills/visual-design/SKILL.md").read_text() == "Мои правила"
    # The ordinary bundled-skill sync used during updates must not replace
    # this independently owned persona or the user's customized design skill.
    assert profiles.seed_profile_skills(target, quiet=True) is not None
    assert (target / "SOUL.md").read_text() == "Моя новая роль"
    assert (target / "skills/visual-design/SKILL.md").read_text() == "Мои правила"


@pytest.mark.parametrize("fields,status", [
    ({"template_id": "../../private"}, 400),
    ({"template_id": "https://example.invalid/agent"}, 400),
    ({"template_version": "unknown"}, 409),
    ({"idempotency_key": "short"}, 400),
    ({"clone_from": "default"}, 400),
    ({"clone_all": True}, 400),
    ({"soul": "replace"}, 400),
    ({"no_skills": True}, 400),
    ({"keep_skills": ["visual-design"]}, 400),
    ({"hub_skills": ["untrusted"]}, 400),
    ({"provider": "custom:chosen"}, 400),
    ({"name": "default"}, 400),
    ({"name": "../escape"}, 400),
])
def test_invalid_request_writes_no_profile(client, fields, status):
    http, root, registrations = client
    response = http.post("/api/profiles", json=request_for(http, **fields))
    assert response.status_code == status, response.text
    assert not (root / "profiles/designer").exists()
    assert registrations == []


def test_existing_profile_and_changed_idempotent_body_are_never_overwritten(client):
    http, root, _ = client
    target = root / "profiles/designer"
    target.mkdir(parents=True)
    (target / "SOUL.md").write_text("Сохранить")
    body = request_for(http)
    assert http.post("/api/profiles", json=body).status_code == 409
    assert (target / "SOUL.md").read_text() == "Сохранить"
    assert http.post("/api/profiles", json={**body, "name": "different"}).status_code == 409
    assert not (root / "profiles/different").exists()


def test_failed_preparation_is_invisible_and_retry_recovers(client, monkeypatch):
    http, root, registrations = client
    from korra_cli import agent_templates, profiles

    body = request_for(http)
    def fail_copy(*args, **kwargs):
        assert not (root / "profiles/designer").exists()
        assert not [p for p in profiles.list_profiles() if not p.is_default]
        raise OSError("synthetic disk failure")

    with monkeypatch.context() as scoped:
        scoped.setattr(agent_templates, "_copy_dist_payload", fail_copy)
        assert http.post("/api/profiles", json=body).status_code == 500
    assert not (root / "profiles/designer").exists()
    assert registrations == []
    # Simulate unpublished leftovers from process termination (not a caught
    # exception): retry owns only the staging folder in its recorded request.
    operation = next((root / "profiles/.template-requests").iterdir())
    (operation / "stage").mkdir()
    (operation / "stage/partial.txt").write_text("interrupted preparation")
    response = http.post("/api/profiles", json=body)
    assert response.status_code == 200, response.text
    assert response.json()["model_set"] is False
    assert not (root / "profiles/designer/partial.txt").exists()


def test_ready_generator_status_is_local_check_without_live_generation(client, monkeypatch):
    http, root, _ = client
    from korra_cli import agent_templates

    checks = []
    monkeypatch.setattr(
        agent_templates,
        "_template_image_generation_available",
        lambda provider: checks.append(provider) or True,
    )
    response = http.post("/api/profiles", json=request_for(http))
    assert response.status_code == 200, response.text
    saved = response.json()
    assert checks == ["openai-codex"]
    assert saved["generation_checked"] is False
    assert saved["generation"]["status"] == "ready"
    assert saved["generation"]["live_tested"] is False
    # Bundled skills may contain static PNG assets (the PDF kit ships Korra
    # marks). They are copied package inputs, not generated output. A profile
    # creation request must not produce an image anywhere outside that package.
    generated = [
        path
        for path in (root / "profiles/designer").rglob("*.png")
        if "skills" not in path.relative_to(root / "profiles/designer").parts
    ]
    assert not generated


def test_generator_status_cannot_be_satisfied_by_an_unrelated_fal_route(monkeypatch):
    from types import SimpleNamespace
    from korra_cli import agent_templates

    monkeypatch.setattr("korra_cli.plugins._ensure_plugins_discovered", lambda: None)
    monkeypatch.setattr(
        "agent.image_gen_registry.get_provider",
        lambda name: SimpleNamespace(is_available=lambda: False),
    )
    # The generic gate may be true because FAL_KEY exists. The template chose
    # openai-codex, so its own provider must still be ready.
    monkeypatch.setattr(
        "tools.image_generation_tool.check_image_generation_requirements",
        lambda: True,
    )
    assert agent_templates._template_image_generation_available("openai-codex") is False


def test_concurrent_retries_publish_one_profile(client):
    http, root, _ = client
    body = request_for(http)
    with ThreadPoolExecutor(max_workers=2) as executor:
        replies = list(executor.map(lambda _: http.post("/api/profiles", json=body), range(2)))
    assert [r.status_code for r in replies] == [200, 200]
    assert replies[0].json() == replies[1].json()
    receipt = json.loads((root / "profiles/designer/.agent-template.json").read_text())
    assert receipt["files"]["skills/visual-design/SKILL.md"]


def test_interruption_after_publication_replays_without_reinstall(client, monkeypatch):
    http, root, _ = client
    from korra_cli import profile_creation

    body = request_for(http)
    atomic_write = profile_creation.atomic_write_text
    def fail_completion(path, *args, **kwargs):
        if path.name == "completed":
            raise OSError("synthetic response interruption")
        return atomic_write(path, *args, **kwargs)
    with monkeypatch.context() as scoped:
        scoped.setattr(profile_creation, "atomic_write_text", fail_completion)
        assert http.post("/api/profiles", json=body).status_code == 500
    (root / "profiles/designer/SOUL.md").write_text("Already edited")
    assert http.post("/api/profiles", json=body).status_code == 200
    assert (root / "profiles/designer/SOUL.md").read_text() == "Already edited"


def test_request_does_not_recreate_a_renamed_agent(client):
    http, root, _ = client
    body = request_for(http)
    assert http.post("/api/profiles", json=body).status_code == 200
    target = root / "profiles/designer"
    target.rename(root / "profiles/renamed")
    assert http.post("/api/profiles", json=body).status_code == 409
    assert not target.exists()


def test_skill_seeding_failure_does_not_publish_an_incomplete_agent(client, monkeypatch):
    http, root, registrations = client
    from korra_cli import profiles

    monkeypatch.setattr(profiles, "seed_profile_skills", lambda *args, **kwargs: None)
    response = http.post("/api/profiles", json=request_for(http))
    assert response.status_code == 500
    assert not (root / "profiles/designer").exists()
    assert registrations == []
