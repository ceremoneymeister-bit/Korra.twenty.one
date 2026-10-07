"""Content/package checks only: no live profile, network or model invocation."""

import hashlib
import json
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


def test_package_contains_methodology_and_declared_font_assets():
    files = {path.relative_to(PACKAGE).as_posix()
             for path in PACKAGE.rglob("*") if path.is_file()}
    required = {
        "distribution.yaml",
        "SOUL.md",
        "skills/visual-design/SKILL.md",
        "skills/visual-design/references/references-and-series.md",
        "skills/visual-design/references/production-and-delivery.md",
        "skills/visual-design/references/presentations-and-social.md",
        "skills/visual-design/references/project-memory.md",
        "skills/visual-design/scripts/review_board.py",
    }
    font_root = PACKAGE / "skills/visual-design/assets/fonts"
    provenance = json.loads((font_root / "SOURCE.json").read_text())
    font_assets = {"README.md", "SOURCE.json", "LICENSE.txt", *provenance["files"]}
    assert files == required | {
        "skills/visual-design/assets/fonts/" + name for name in font_assets
    }


def test_bundled_fonts_match_provenance_and_render_cyrillic():
    from PIL import ImageFont

    root = PACKAGE / "skills/visual-design/assets/fonts"
    provenance = json.loads((root / "SOURCE.json").read_text())
    assert provenance["source_url"].startswith("https://github.com/rsms/inter/")
    assert "SIL OPEN FONT LICENSE Version 1.1" in (root / "LICENSE.txt").read_text()
    for name, expected in provenance["files"].items():
        assert Path(name).name == name and name.endswith(".ttf")
        path = root / name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected["sha256"]
        font = ImageFont.truetype(str(path), 48)
        assert font.getname()[0] == expected["family"]
        assert font.getname()[1] == expected["style"]
        missing = bytes(font.getmask(chr(0x10FFFF)))
        for character in "АБВЁЖЙФЦЧШЩЪЫЬЭЮЯабвёжйфцчшщъыьэюя":
            glyph = bytes(font.getmask(character))
            assert glyph and glyph != missing, (name, character)


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


def test_all_methodology_documents_are_reachable_from_the_role():
    """A packaged reference is useful only if the installed role can lead to it."""
    pending = [(PACKAGE / "SOUL.md").resolve()]
    visited = set()
    while pending:
        source = pending.pop()
        if source in visited:
            continue
        visited.add(source)
        for href in re.findall(r"\[[^\]\n]+\]\(([^)]+)\)", source.read_text()):
            target = (source.parent / href.split("#", 1)[0]).resolve()
            assert target.is_relative_to(PACKAGE.resolve())
            if target.suffix == ".md":
                pending.append(target)
    assert visited == {path.resolve() for path in PACKAGE.rglob("*.md")}


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


def test_review_board_shows_both_inputs_and_preserves_existing_files(tmp_path, monkeypatch):
    import importlib.util
    import sys
    from PIL import Image

    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    script = PACKAGE / "skills/visual-design/scripts/review_board.py"
    spec = importlib.util.spec_from_file_location("designer_review_board", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    candidate, reference, output = (
        tmp_path / name for name in ("result.png", "source.png", "board.png")
    )
    Image.new("RGB", (400, 600), "red").save(candidate)
    Image.new("RGBA", (600, 400), (0, 0, 255, 255)).save(reference)
    before = [path.read_bytes() for path in (candidate, reference)]
    module.build_board(candidate, [reference], output)
    with Image.open(output) as board:
        assert board.width > board.height
        assert board.getpixel((765, 1100)) == (255, 0, 0)
        assert board.getpixel((1955, 1100)) == (0, 0, 255)
    assert [path.read_bytes() for path in (candidate, reference)] == before
    saved = output.read_bytes()
    with pytest.raises(FileExistsError):
        module.build_board(candidate, [reference], output)
    assert output.read_bytes() == saved
