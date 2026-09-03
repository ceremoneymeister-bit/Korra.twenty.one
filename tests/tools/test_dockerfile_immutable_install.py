"""Contract tests for the Docker image's immutable /opt/hermes install tree."""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = REPO_ROOT / "Dockerfile"


def _dockerfile_text() -> str:
    return DOCKERFILE.read_text()


def test_dockerfile_makes_opt_hermes_readonly_for_hermes_user() -> None:
    text = _dockerfile_text()

    # --chmod on the source COPY bakes read-only perms at copy time instead
    # of a separate chmod -R pass (which walked ~30k files — #49113).
    assert "COPY --link --chmod=a+rX,go-w . ." in text
    # The old tree-walking passes must not be present.
    #
    # Korra: проверка стала точной по пути. Раньше здесь стояло совпадение по
    # подстроке, и оно запрещало ЛЮБОЙ путь с префиксом /opt/hermes — включая
    # /opt/hermes/models, где лежат четыре файла весов whisper. Гейт написан
    # про стоимость обхода дерева (~30k файлов, #49113), а не про слово chmod,
    # поэтому запрет выражен по смыслу:
    #
    #   запрещено  — корень дерева установки и его тяжёлые подкаталоги
    #                (.venv, .playwright, node_modules и прочие дот-каталоги);
    #                именно они и дают те самые тридцать тысяч файлов;
    #   разрешено  — маленькие целевые подкаталоги вроде /opt/hermes/models.
    #
    # Дот-каталоги под запретом СОЗНАТЕЛЬНО: это те же тяжёлые деревья, что
    # перечислены поимённо в test_dockerfile_does_not_chown_install_trees_to_hermes.
    # Сосед по префиксу (/opt/hermes-cache) под запрет не попадает: это другой
    # путь, а не дерево установки.
    heavy = r"/opt/hermes(?:/(?:\.[\w.-]+|node_modules|ui-tui|gateway))?/?(?=[\s;&|)]|$)"

    # Гейт обязан кусаться ровно там, где задумано, и молчать там, где нет.
    # Без этих строк регулярка тихо разъедется при следующей правке путей.
    def _bites(path: str) -> bool:
        return bool(re.search(rf"chmod\s+-R\s+a\+rX\s+{heavy}", f"RUN chmod -R a+rX {path} && x"))

    assert _bites("/opt/hermes")                    # корень
    assert _bites("/opt/hermes/")                   # корень со слешем
    assert _bites("/opt/hermes/.venv")              # тяжёлое дерево
    assert _bites("/opt/hermes/.playwright")        # тяжёлое дерево
    assert _bites("/opt/hermes/node_modules")       # тяжёлое дерево
    assert not _bites("/opt/hermes/models")         # четыре файла весов
    assert not _bites("/opt/hermes/models/whisper")
    assert not _bites("/opt/hermes-cache")          # другой путь, не дерево установки

    assert not re.search(rf"chown\s+-R\s+root:root\s+{heavy}", text)
    assert not re.search(rf"chmod\s+-R\s+a\+rX\s+{heavy}", text)
    assert not re.search(rf"chmod\s+-R\s+a-w\s+{heavy}", text)


def test_dockerfile_does_not_chown_install_trees_to_hermes() -> None:
    text = _dockerfile_text()
    forbidden_patterns = (
        r"chown\s+-R\s+hermes:hermes\s+/opt/hermes/\.venv",
        r"chown\s+-R\s+hermes:hermes\s+/opt/hermes/ui-tui",
        r"chown\s+-R\s+hermes:hermes\s+/opt/hermes/gateway",
        r"chown\s+-R\s+hermes:hermes\s+/opt/hermes/node_modules",
    )
    for pattern in forbidden_patterns:
        assert not re.search(pattern, text), (
            "runtime install trees under /opt/hermes must stay immutable; "
            f"found forbidden pattern {pattern!r}"
        )


def test_dockerfile_bakes_code_scoped_install_method_stamp() -> None:
    """The 'docker' install-method stamp is baked next to the code.

    detect_install_method() reads the code-scoped stamp
    (/opt/hermes/.install_method) first; baking it at build time keeps the
    published image self-identifying as 'docker' WITHOUT writing into the
    shared $HERMES_HOME data volume (which a host install may also use).
    The stamp is created by root in the shim-wiring RUN block; the hermes
    user can't modify it (go-w from the --chmod on the source COPY).
    """
    text = _dockerfile_text()
    assert "printf 'docker\\n' > /opt/hermes/.install_method" in text

    # The stamp must be in the RUN block that wires the exec shim.
    shim_block = re.search(
        r"RUN mkdir -p /opt/hermes/bin && \\\n"
        r"(?:.*\\\n)+?"
        r"\s+printf 'docker\\n' > /opt/hermes/\.install_method",
        text,
    )
    assert shim_block, "install-method stamp must be in the shim-wiring RUN block"


def test_dockerfile_redirects_lazy_installs_to_durable_target() -> None:
    """Immutable image seals the venv but redirects lazy installs to the
    writable data volume, so opt-in backends still install at first use
    without being able to break the sealed core.

    Guards the contract between the Dockerfile env var, the stage2-hook
    seeding, and tools/lazy_deps.py — these three must agree on the path.
    """
    text = _dockerfile_text()
    target = "/opt/data/lazy-packages"

    # The redirect target must be set AND must live under the data volume,
    # never under the immutable /opt/hermes tree.
    assert f"ENV HERMES_LAZY_INSTALL_TARGET={target}" in text
    assert target.startswith("/opt/data/"), "target must be on the durable volume"
    assert "ENV HERMES_LAZY_INSTALL_TARGET=/opt/hermes" not in text

    # The seal flag must still be present — the redirect rides on top of it,
    # it does not replace it.
    assert "ENV HERMES_DISABLE_LAZY_INSTALLS=1" in text

    # stage2-hook must seed + chown the target dir so first-use installs
    # succeed as the unprivileged hermes runtime user.
    stage2 = (REPO_ROOT / "docker" / "stage2-hook.sh").read_text()
    assert '"$HERMES_HOME/lazy-packages"' in stage2, (
        "stage2-hook.sh must create the lazy-packages dir on the data volume"
    )
    assert "lazy-packages" in stage2.split("for sub in", 1)[1].split(";", 1)[0], (
        "lazy-packages must be in the per-boot chown subdir list so it stays "
        "hermes-owned"
    )


def test_dockerfile_bakes_photon_sidecar_deps() -> None:
    """The Photon sidecar's node_modules must be baked at build time (NS-606).

    The install tree is immutable at runtime, so a lazy `npm ci` on first
    connect would hit EROFS. Baking the deps (from the committed lockfile,
    which also runs the spectrum-ts postinstall patch) makes the hosted
    happy path install-free. Guards the contract between the Dockerfile
    and plugins/platforms/photon/sidecar_paths.resolve_sidecar_dir, which
    runs in place only when the baked deps exist and match the lockfile.
    """
    text = _dockerfile_text()

    assert "plugins/platforms/photon/sidecar/package-lock.json" in text
    assert re.search(
        r"RUN cd plugins/platforms/photon/sidecar && \\\n\s+npm ci", text
    ), "sidecar deps must be installed with `npm ci` (deterministic, runs postinstall patch)"
    # Immutability contract: never chown the sidecar tree to the runtime user.
    assert not re.search(
        r"chown\s+-R\s+hermes:hermes\s+/opt/hermes/plugins", text
    )
