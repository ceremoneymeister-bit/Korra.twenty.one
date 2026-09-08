"""Behavioral coverage for exact-artifact handoff and offline release gating."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("release_ci", ROOT / "docs/client-deploy/ci-acceptance.py")
ci = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ci)
IMAGE_ID = "sha256:" + "a" * 64
REVISION = "b" * 40
INFO = {"Id": IMAGE_ID, "Architecture": "amd64", "Config": {
    "Env": ["KORRA_HOME=/opt/data"], "Labels": {"org.opencontainers.image.revision": REVISION}}}


@pytest.fixture
def artifact(tmp_path, monkeypatch):
    calls = []

    def docker(*args, **kwargs):
        calls.append(args)
        if args[:3] == ("docker", "image", "inspect"):
            return json.dumps([INFO])
        if args[:3] == ("docker", "image", "save"):
            Path(args[4]).write_bytes(b"synthetic image archive")
        return ""

    monkeypatch.setattr(ci, "run", docker)
    directory = tmp_path / "artifact"
    manifest = ci.pack("mutable:tag", directory, REVISION, "amd64")
    return directory, manifest, calls


def test_pack_saves_image_by_id_and_handoff_loads_archive(artifact):
    directory, manifest, calls = artifact
    assert calls[1][-1] == IMAGE_ID
    calls.clear()
    assert ci.load_artifact(directory, REVISION, "amd64") == manifest
    assert calls[0] == ("docker", "image", "load", "--input", str(directory / "image.tar"))
    assert calls[1][-1] == IMAGE_ID


@pytest.mark.parametrize("damage", ["missing_archive", "changed_archive", "wrong_revision", "wrong_arch", "missing_image_id"])
def test_invalid_artifact_fails_before_loading(artifact, damage):
    directory, manifest, calls = artifact
    if damage == "missing_archive":
        (directory / "image.tar").unlink()
    elif damage == "changed_archive":
        (directory / "image.tar").write_bytes(b"another image")
    else:
        key = {"wrong_revision": "revision", "wrong_arch": "arch", "missing_image_id": "image_id"}[damage]
        manifest[key] = "wrong"
        (directory / "manifest.json").write_text(json.dumps(manifest))
    calls.clear()
    with pytest.raises((ci.AcceptanceError, OSError)):
        ci.load_artifact(directory, REVISION, "amd64")
    assert not calls


def test_loading_archive_verifies_actual_loaded_image(artifact, monkeypatch):
    directory, _, _ = artifact
    monkeypatch.setattr(ci, "image_info", lambda _: {**INFO, "Id": "sha256:" + "c" * 64})
    with pytest.raises(ci.AcceptanceError, match="differs from build artifact"):
        ci.load_artifact(directory, REVISION, "amd64")


@pytest.mark.parametrize("bad_receipt", [None, {"accepted": False}, {"accepted": True, "artifact": {"image_id": "other"}}])
def test_publication_handoff_requires_matching_success_receipt(artifact, tmp_path, bad_receipt):
    directory, _, calls = artifact
    receipt = tmp_path / "receipt.json"
    if bad_receipt is not None:
        receipt.write_text(json.dumps(bad_receipt))
    calls.clear()
    with pytest.raises((ci.AcceptanceError, OSError)):
        ci.load_artifact(directory, REVISION, "amd64", receipt)
    assert not calls


def test_failed_gate_cannot_reuse_previous_success(artifact, tmp_path, monkeypatch):
    directory, manifest, _ = artifact
    receipt = tmp_path / "receipt.json"
    receipt.write_text(json.dumps({"accepted": True, "artifact": manifest}))

    def fail(_image, revision=""):
        raise ci.AcceptanceError("candidate regression")

    monkeypatch.setattr(ci, "check_image", fail)
    with pytest.raises(ci.AcceptanceError, match="candidate regression"):
        ci.accept(directory, REVISION, "amd64", receipt)
    assert not receipt.exists()
    with pytest.raises(OSError):
        ci.load_artifact(directory, REVISION, "amd64", receipt)


def test_success_receipt_is_bound_to_loaded_image(artifact, tmp_path, monkeypatch):
    directory, manifest, _ = artifact
    checked = []
    monkeypatch.setattr(ci, "check_image",
                        lambda image, revision="": checked.append((image, revision)) or ["synthetic acceptance"])
    receipt = tmp_path / "receipt.json"
    result = ci.accept(directory, REVISION, "amd64", receipt)
    # Приёмка сверяет заметки к выпуску с ревизией собранного артефакта.
    assert checked == [(manifest["image_id"], REVISION)]
    assert result["artifact"] == manifest
    assert ci.load_artifact(directory, REVISION, "amd64", receipt) == manifest


@pytest.mark.parametrize("panel,api", [
    ("<html>SPA fallback</html>", {"status": "ok", "version": "1"}),
    ({"gateway_running": False, "version": "1"}, {"status": "ok", "version": "1"}),
    ({"gateway_running": True, "version": "1"}, {"status": "starting", "version": "1"}),
    ({"gateway_running": True, "version": "1"}, "<html>SPA fallback</html>"),
])
def test_http_200_is_not_application_readiness(panel, api):
    with pytest.raises(ci.AcceptanceError):
        ci.validate_health(panel, api)


def stream(reason="error", message="Откройте «Ключи»", done=True):
    item = {"choices": [{"finish_reason": reason}], "error": {"message": message}}
    return "data: " + json.dumps(item) + "\n\n" + ("data: [DONE]\n" if done else "")


def test_missing_provider_reports_actionable_error():
    ci.validate_sse(stream())
    ci.validate_health({"gateway_running": True, "version": "1"}, {"status": "ok", "version": "1"})


@pytest.mark.parametrize("body", ["", "data: [DONE]", stream(reason="stop"), stream(message=""), stream(done=False)])
def test_missing_provider_cannot_appear_successful_or_empty(body):
    with pytest.raises(ci.AcceptanceError):
        ci.validate_sse(body)


def test_ci_container_is_offline_bounded_and_has_no_host_state():
    command = ci.container_command("k21-release-ci-" + "a" * 32, INFO)
    for flag, value in [("--network", "none"), ("--cpus", "4"), ("--memory", "4g"),
                        ("--memory-swap", "4g"), ("--pids-limit", "256"), ("--pull", "never")]:
        assert command[command.index(flag) + 1] == value
    assert command[-3:] == [IMAGE_ID, "gateway", "run"]
    assert command[command.index("--tmpfs") + 1].startswith("/opt/data:")
    assert not {"--volume", "-v", "--mount", "--publish", "-p", "--env-file"}.intersection(command)


@pytest.mark.parametrize("name", ["korra21", "korra", "k21-release-ci-prod", "korra-victoria"])
def test_ci_namespace_cannot_target_live_containers(name):
    with pytest.raises(ci.AcceptanceError):
        ci.container_command(name, INFO)


def test_probe_failure_cleans_only_generated_container(monkeypatch):
    commands = []

    def docker(*args, **kwargs):
        commands.append(args)
        if args[:3] == ("docker", "image", "inspect"):
            return json.dumps([INFO])
        if args[:2] == ("docker", "inspect"):
            return json.dumps([{"State": {"Running": True}, "Image": IMAGE_ID}])
        if args[:2] == ("docker", "exec"):
            if args[-1] == "health":
                return json.dumps({"panel": {"gateway_running": True, "version": "1"},
                                   "api": {"status": "ok", "version": "1"}})
            raise ci.AcceptanceError("bootstrap regression")
        return ""

    monkeypatch.setattr(ci, "run", docker)
    with pytest.raises(ci.AcceptanceError, match="bootstrap regression"):
        ci.check_image(IMAGE_ID)
    create = next(c for c in commands if c[:2] == ("docker", "run"))
    name = create[create.index("--name") + 1]
    assert name.startswith("k21-release-ci-")
    assert commands[-1] == ("docker", "rm", "--force", "--volumes", name)


def image_daemon(release_note, help_text="usage: korra"):
    """Docker, отвечающий как на живом образе. Пробы подменены целиком."""
    def docker(*args, **kwargs):
        if args[:3] == ("docker", "image", "inspect"):
            return json.dumps([INFO])
        if args[:2] == ("docker", "inspect"):
            return json.dumps([{"State": {"Running": True}, "Image": IMAGE_ID}])
        if args[:2] == ("docker", "exec"):
            if args[-1] == "health":
                return json.dumps({"panel": {"gateway_running": True, "version": "1"},
                                   "api": {"status": "ok", "version": "1"}})
            if args[-1] == "release":
                return json.dumps(release_note)
            if args[-1] == "chat":
                return stream()
            if args[-1] == "--help":
                return help_text
        return ""
    return docker


def test_release_notes_must_be_stamped_with_the_build_revision():
    ci.validate_release_note({"release_id": "K21-2026.09.08", "revision": REVISION}, REVISION)
    for note in ({}, {"release_id": ""}, {"release_id": "K21-X", "revision": ""},
                 {"release_id": "K21-X", "revision": "c" * 40}, "не словарь"):
        with pytest.raises(ci.AcceptanceError):
            ci.validate_release_note(note, REVISION)


def test_acceptance_refuses_an_image_whose_notes_name_another_revision(monkeypatch):
    """Заметки описывают собранный образ, иначе связь «заметки → код → digest»
    держаться не на чем. Ревизия, написанная руками, обязана быть неверной:
    коммит с самой записью её и меняет — так K21-2026.09.08 и уехал бы в
    реестр с ревизией предыдущего дерева."""
    monkeypatch.setattr(ci, "run", image_daemon({"release_id": "K21-2026.09.08", "revision": "c" * 40}))
    with pytest.raises(ci.AcceptanceError, match="Release notes revision"):
        ci.check_image(IMAGE_ID, revision=REVISION)


def test_acceptance_confirms_notes_stamped_by_this_build(monkeypatch):
    monkeypatch.setattr(ci, "run", image_daemon({"release_id": "K21-2026.09.08", "revision": REVISION}))
    assert "release notes stamped with this build revision" in ci.check_image(IMAGE_ID, revision=REVISION)


@pytest.mark.parametrize("gate_result", ["failure", "cancelled", "skipped"])
def test_workflow_dag_blocks_all_publication_after_failed_acceptance(gate_result):
    # YAML is executable CI configuration. Evaluate dependency propagation;
    # no Python/source-text regex checks or mirrored implementation strings.
    jobs = yaml.safe_load((ROOT / ".github/workflows/docker.yml").read_text())["jobs"]
    results = {"detect": "success", "build": "success", "acceptance": gate_result}
    for name in ("publish", "merge"):
        job = jobs[name]
        assert not any(override in job.get("if", "") for override in ("always()", "failure()", "!cancelled()"))
        results[name] = "success" if all(results[need] == "success" for need in job["needs"]) else "skipped"
    assert results["publish"] == results["merge"] == "skipped"


def test_cli_missing_artifact_fails_closed(tmp_path):
    assert ci.main(["load", "--directory", str(tmp_path), "--revision", REVISION,
                    "--arch", "amd64", "--receipt", str(tmp_path / "receipt.json")]) == 1


@pytest.mark.parametrize("paths,gh_exit,expected", [
    ("docs/client-deploy/updater.py", "0", "true"),
    ("docs/client-deploy/ci-acceptance.py", "0", "true"),
    ("tests/deployment/test_update.py", "0", "true"),
    ("docs/README.md", "0", "false"),
    ("", "1", "true"),
    ("", "0", "true"),
])
def test_pr_deployment_only_runs_actual_workflow_gate(tmp_path, paths, gh_exit, expected):
    jobs = yaml.safe_load((ROOT / ".github/workflows/docker.yml").read_text())["jobs"]
    gate = next(step for step in jobs["detect"]["steps"] if step.get("id") == "gate")
    # Run the actual YAML shell step with a synthetic GitHub compare response.
    # This catches miswiring of deployment_meta into the workflow output.
    gh = tmp_path / "gh"
    gh.write_text('#!/bin/sh\nprintf "%s\\n" "$CI_CHANGED_FILES"\nexit "$CI_GH_EXIT"\n', encoding="utf-8")
    gh.chmod(0o755)
    output = tmp_path / "output"
    env = {**os.environ, "DOCKER": "false", "EVENT_NAME": "pull_request", "BASE_SHA": "a" * 40,
           "HEAD_SHA": "b" * 40, "REPO": "synthetic/repo", "GITHUB_OUTPUT": str(output),
           "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"], "CI_CHANGED_FILES": paths,
           "CI_GH_EXIT": gh_exit}
    result = subprocess.run(["bash", "-c", gate["run"]], cwd=ROOT, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert output.read_text().strip() == "build=" + expected


@pytest.mark.parametrize("count", [0, 2])
def test_publish_shell_refuses_missing_or_ambiguous_receipts(tmp_path, count):
    jobs = yaml.safe_load((ROOT / ".github/workflows/docker.yml").read_text())["jobs"]
    step = next(step for step in jobs["publish"]["steps"] if step.get("id") == "accepted")
    receipt_dir = tmp_path / "receipts"
    receipt_dir.mkdir()
    for index in range(count):
        (receipt_dir / f"{index}.json").write_text("{}", encoding="utf-8")
    output = tmp_path / "output"
    result = subprocess.run(["bash", "-c", step["run"]], cwd=ROOT, capture_output=True, text=True,
                            env={**os.environ, "RECEIPT_DIR": str(receipt_dir), "GITHUB_OUTPUT": str(output)})
    assert result.returncode != 0
    assert "Expected exactly one acceptance receipt" in result.stdout
    assert not output.exists()
