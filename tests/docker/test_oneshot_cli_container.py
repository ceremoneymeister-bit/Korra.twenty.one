"""K21-038: одноразовый `korra import` в контейнере не должен мешать сам себе.

`docker run <image> korra import ARCHIVE` проходил полный супервизируемый
старт: s6 поднимал dashboard и шлюзы профилей, те открывали целевой DATA, и
импорт на полностью остановленном контуре падал «не удалось проверить всех
держателей DATA». Работал только обход `--entrypoint /opt/hermes/.venv/bin/korra`
(миграция Марины Павловой 11.09.2026).

Сборка образа тестам недоступна, поэтому скрипты проверяются как скрипты:
классификатор — напрямую, а диспетчер и обёртка — на копии с подменённым
префиксом путей и заглушками вместо /init, stage2 и s6-setuidgid. Проверяется
именно та ветка, которую выберет реальный файл.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

DOCKER_DIR = Path(__file__).resolve().parents[2] / "docker"
ROLE_SCRIPT = DOCKER_DIR / "cli-role.sh"


def role(mode: str, *argv: str) -> int:
    return subprocess.run(
        ["sh", str(ROLE_SCRIPT), mode, *argv], capture_output=True, text=True
    ).returncode


@pytest.mark.parametrize(
    "argv",
    [
        ("korra", "import", "archive.zip"),
        ("import", "archive.zip"),
        ("korra", "backup"),
        ("korra", "config", "get", "model.provider"),
        ("korra", "-p", "worker", "import", "archive.zip"),
        ("korra", "--profile=worker", "backup"),
    ],
)
def test_one_shot_cli_commands_need_no_services(argv):
    assert role("oneshot", *argv) == 0


@pytest.mark.parametrize(
    "argv",
    [
        ("korra",),
        ("korra", "chat", "-q", "привет"),
        ("gateway", "run"),
        ("korra", "dashboard", "--port", "9119"),
        ("sleep", "infinity"),
        ("bash",),
        ("korra", "--tui"),
        (),
    ],
)
def test_everything_else_still_starts_supervised(argv):
    assert role("oneshot", *argv) != 0


def test_only_import_keeps_root():
    assert role("needs-root", "korra", "import", "archive.zip") == 0
    assert role("needs-root", "korra", "-p", "worker", "import", "a.zip") == 0
    assert role("needs-root", "korra", "backup") != 0
    assert role("needs-root", "korra", "config", "get", "x") != 0
    assert role("needs-root", "korra", "chat") != 0


@pytest.fixture
def fake_image(tmp_path):
    """Копия скриптов образа с подменёнными абсолютными путями и заглушками."""
    root = tmp_path / "opt" / "hermes"
    (root / "docker").mkdir(parents=True)
    (tmp_path / "opt" / "data").mkdir(parents=True)
    (root / ".venv" / "bin").mkdir(parents=True)
    (root / ".venv" / "bin" / "activate").write_text("# stub\n")

    for name in ("entrypoint-dispatch.sh", "main-wrapper.sh", "cli-role.sh"):
        text = (DOCKER_DIR / name).read_text(encoding="utf-8")
        text = text.replace("/opt/hermes", str(root)).replace(
            "/opt/data", str(tmp_path / "opt" / "data")
        )
        text = text.replace("exec /init ", f"exec {tmp_path}/init ")
        target = root / "docker" / name
        target.write_text(text, encoding="utf-8")
        target.chmod(0o755)

    for name, body in (
        ("init", 'echo "INIT $*"\n'),
        ("s6-setuidgid", 'echo "DROPPED $1"\nshift\nexec "$@"\n'),
        ("hermes", 'echo "HERMES $*"\n'),
        ("korra", 'echo "KORRA $*"\n'),
        ("with-contenv", 'exec "$@"\n'),
        # Контейнер стартует от root, и пользователь hermes в образе есть.
        # Без этой заглушки скрипт видел бы того, кто запустил pytest: на
        # раннере это uid 10003 без записи `hermes` в passwd, и обе проверки
        # уходили в апстримную ветку «arbitrary --user» до сброса привилегий.
        # Неожиданная форма вызова — громкий отказ, чтобы заглушка не прятала
        # изменение в самих скриптах.
        (
            "id",
            'case "$*" in\n'
            '  "-u") echo 0 ;;\n'
            '  "-u hermes") echo 10000 ;;\n'
            '  *) echo "id stub: unexpected args: $*" >&2; exit 2 ;;\n'
            'esac\n',
        ),
    ):
        script = tmp_path / name
        script.write_text(f"#!/bin/sh\n{body}", encoding="utf-8")
        script.chmod(0o755)

    stage2 = root / "docker" / "stage2-hook.sh"
    stage2.write_text(
        '#!/bin/sh\necho "STAGE2 oneshot=${KORRA_ONESHOT_CLI:-0}"\n', encoding="utf-8"
    )
    stage2.chmod(0o755)
    return root, tmp_path


def run_script(script: Path, argv, tmp_path) -> str:
    env = dict(os.environ)
    env["PATH"] = f"{tmp_path}:{env['PATH']}"
    env["HERMES_HOME"] = str(tmp_path / "opt" / "data")
    result = subprocess.run(
        ["sh", str(script), *argv], capture_output=True, text=True, env=env, timeout=60
    )
    return result.stdout + result.stderr


def test_dispatcher_skips_init_for_import(fake_image):
    root, tmp_path = fake_image
    output = run_script(
        root / "docker" / "entrypoint-dispatch.sh", ["korra", "import", "a.zip"], tmp_path
    )
    assert "STAGE2 oneshot=1" in output
    assert "INIT" not in output
    # Ветка должна быть именно одноразовой, а не запасной «мы не PID 1»: в
    # настоящем контейнере PID 1 наш, и до правки там исполнялся /init.
    assert "not PID 1" not in output
    assert "K21-038" in output


@pytest.mark.parametrize("argv", [["chat", "-q", "привет"], ["gateway", "run"]])
def test_dispatcher_keeps_the_supervised_path_for_everything_else(fake_image, argv):
    root, tmp_path = fake_image
    output = run_script(root / "docker" / "entrypoint-dispatch.sh", argv, tmp_path)
    # В тесте скрипт не PID 1, поэтому супервизируемая ветка уходит в штатный
    # запасной путь — по его предупреждению и видно, что ветка выбрана та.
    assert "not PID 1" in output
    assert "K21-038" not in output


def test_wrapper_keeps_root_for_import_and_drops_for_backup(fake_image):
    root, tmp_path = fake_image
    wrapper = root / "docker" / "main-wrapper.sh"
    assert "DROPPED" not in run_script(wrapper, ["korra", "import", "a.zip"], tmp_path)
    assert "DROPPED" in run_script(wrapper, ["korra", "backup"], tmp_path)
    assert "DROPPED" in run_script(wrapper, ["chat", "-q", "привет"], tmp_path)
