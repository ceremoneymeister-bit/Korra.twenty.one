"""Content/package checks only: no live profile, network or model invocation."""

import re
from pathlib import Path

import pytest
import yaml


WORKSPACE = Path(__file__).resolve().parents[2]
PACKAGE = WORKSPACE / "korra_cli/data/agent_templates/designer"

from korra_cli.profile_distribution import (  # noqa: E402
    _copy_dist_payload,
    read_manifest,
)


def test_manifest_matches_explicit_payload():
    manifest = read_manifest(PACKAGE)
    assert manifest is not None
    assert manifest.name == "designer"
    assert manifest.version
    assert manifest.env_requires == []
    assert set(manifest.owned_paths()) == {
        "SOUL.md", "skills/visual-design", "distribution.yaml"
    }
    for relative in manifest.owned_paths():
        source = PACKAGE / relative
        assert source.exists()
        assert source.resolve().is_relative_to(PACKAGE.resolve())
    assert not any(path.is_symlink() for path in PACKAGE.rglob("*"))


def test_package_contains_only_reviewable_content():
    files = {path.relative_to(PACKAGE).as_posix()
             for path in PACKAGE.rglob("*") if path.is_file()}
    assert files == {
        "distribution.yaml",
        "SOUL.md",
        "skills/visual-design/SKILL.md",
        "skills/visual-design/references/references-and-series.md",
        "skills/visual-design/references/production-and-delivery.md",
        "skills/visual-design/references/presentations-and-social.md",
        "skills/visual-design/references/project-memory.md",
    }


def test_skill_frontmatter():
    skill = PACKAGE / "skills/visual-design/SKILL.md"
    match = re.match(r"\A---\n(.*?)\n---\n", skill.read_text(), re.S)
    assert match is not None
    metadata = yaml.safe_load(match.group(1))
    assert metadata["name"] == skill.parent.name
    assert isinstance(metadata["description"], str)
    assert 20 < len(metadata["description"]) <= 60
    assert metadata["description"].endswith(".")
    assert set(metadata) == {"name", "description"}


@pytest.mark.parametrize("source", sorted(PACKAGE.rglob("*.md")),
                         ids=lambda path: str(path.relative_to(PACKAGE)))
def test_local_content_links_resolve(source):
    for href in re.findall(r"\[[^\]\n]+\]\(([^)]+)\)", source.read_text()):
        assert "://" not in href, "Candidate content must be self-contained"
        target = (source.parent / href.split("#", 1)[0]).resolve()
        assert target.is_relative_to(WORKSPACE.resolve())
        assert target.is_file(), f"Broken link in {source}: {href}"
        if source.name != "README.md":
            assert target.is_relative_to(PACKAGE.resolve()), (
                "Installed role/skill must not depend on author workspace docs"
            )


def test_native_payload_copy_excludes_author_docs_and_preserves_user_data(tmp_path):
    """Exercise the existing copier, not CLI installation or provider bootstrap."""
    target = tmp_path / "synthetic-profile"
    user_files = {
        "workspace/design/project/source.txt": "synthetic reference",
        "memories/USER.md": "synthetic user preference",
        "local/style.md": "synthetic custom rule",
        "skills/user-skill/SKILL.md": "synthetic user skill",
        "config.yaml": "test: true\n",
        "auth.json": '{"synthetic": true}\n',
    }
    for relative, content in user_files.items():
        path = target / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    manifest = read_manifest(PACKAGE)
    assert manifest is not None
    _copy_dist_payload(PACKAGE, target, manifest, preserve_config=True)
    _copy_dist_payload(PACKAGE, target, manifest, preserve_config=True)
    for relative, content in user_files.items():
        assert (target / relative).read_text() == content
    for owned in [PACKAGE / "SOUL.md", PACKAGE / "skills/visual-design"]:
        for source in ([owned] if owned.is_file() else owned.rglob("*")):
            if source.is_file():
                relative = source.relative_to(PACKAGE)
                assert (target / relative).read_bytes() == source.read_bytes()
    assert not (target / "README.md").exists()
    assert not (target / "cron").exists()
    assert read_manifest(target).version == manifest.version
