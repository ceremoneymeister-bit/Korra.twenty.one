"""K21-276: a transient config.yaml read error neither wipes the file nor sticks until restart."""

import errno

import pytest
import yaml

import korra_cli.config as config_mod
from korra_cli.config import (
    FailedConfigRead,
    atomic_config_write,
    load_config,
    load_config_readonly,
    read_raw_config,
    save_config,
)

_CONFIG = """# hand-tuned
model:
  default: my-org/custom-model
display:
  skin: mono
terminal:
  backend: docker
approvals:
  deny:
  - rm -rf /
"""


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "config.yaml").write_text(_CONFIG, encoding="utf-8")
    config_mod._LOAD_CONFIG_CACHE.clear()
    config_mod._RAW_CONFIG_CACHE.clear()
    config_mod._LAST_EXPANDED_CONFIG_BY_PATH.clear()
    config_mod._CONFIG_PARSE_WARNED.clear()
    yield tmp_path
    config_mod._LOAD_CONFIG_CACHE.clear()
    config_mod._RAW_CONFIG_CACHE.clear()
    config_mod._LAST_EXPANDED_CONFIG_BY_PATH.clear()


class _Faults:
    """Fails the chosen parse of config.yaml with a transient EMFILE (the file stays intact)."""

    def __init__(self, monkeypatch, path):
        self.path, self.count, self.fail_at = str(path), 0, 0
        real = config_mod.fast_safe_load

        def flaky(stream):
            if getattr(stream, "name", None) == self.path:
                self.count += 1
                if self.count == self.fail_at:
                    raise OSError(errno.EMFILE, "Too many open files")
            return real(stream)

        monkeypatch.setattr(config_mod, "fast_safe_load", flaky)

    def arm(self, fail_at=1):
        self.count, self.fail_at = 0, fail_at


@pytest.fixture
def faults(home, monkeypatch):
    return _Faults(monkeypatch, home / "config.yaml")


def test_failed_load_is_marked_and_not_cached(home, faults):
    faults.arm(1)
    failed = load_config()
    assert isinstance(failed, FailedConfigRead)
    assert failed["display"]["skin"] != "mono"

    again = load_config()
    assert not isinstance(again, FailedConfigRead)
    assert again["display"]["skin"] == "mono"
    assert load_config_readonly()["display"]["skin"] == "mono"


def test_defaults_from_a_failed_read_never_become_last_known_good(home, faults):
    faults.arm(1)
    load_config()
    assert str(home / "config.yaml") not in config_mod._LAST_EXPANDED_CONFIG_BY_PATH


def test_failed_read_with_last_known_good_is_not_cached_either(home, faults):
    assert load_config()["display"]["skin"] == "mono"
    (home / "config.yaml").write_text(_CONFIG + "# touched\n", encoding="utf-8")
    faults.arm(1)
    served = load_config()
    assert isinstance(served, FailedConfigRead)
    assert served["display"]["skin"] == "mono"  # last-known-good keeps serving the settings
    assert not isinstance(load_config(), FailedConfigRead)


def test_failed_raw_read_is_marked_and_retried(home, faults):
    faults.arm(1)
    assert isinstance(read_raw_config(), FailedConfigRead)
    assert read_raw_config()["display"]["skin"] == "mono"


@pytest.mark.parametrize("failing_read", [1, 2, 3])
def test_no_single_transient_read_error_reaches_the_file(home, faults, failing_read):
    path = home / "config.yaml"
    before = path.read_bytes()
    faults.arm(failing_read)
    try:
        config = load_config()
        config["display"]["skin"] = "default"
        save_config(config)
    except RuntimeError:
        assert path.read_bytes() == before
    else:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert raw["terminal"]["backend"] == "docker"
        assert raw["approvals"]["deny"] == ["rm -rf /"]
        assert raw["model"]["default"] == "my-org/custom-model"


def test_save_refuses_a_failed_load_and_leaves_the_file_untouched(home, faults):
    path = home / "config.yaml"
    before = path.read_bytes()
    faults.arm(1)
    config = load_config()
    config["display"]["skin"] = "default"
    with pytest.raises(RuntimeError, match="не удалось прочитать"):
        save_config(config)
    assert path.read_bytes() == before


def test_partial_save_refuses_when_the_raw_read_fails(home, faults):
    path = home / "config.yaml"
    before = path.read_bytes()
    faults.arm(2)  # the strict pre-write check parses first; the raw read for paths is the second
    with pytest.raises(RuntimeError, match="не удалось прочитать"):
        save_config({"display": {"skin": "default"}}, merge_existing=True)
    assert path.read_bytes() == before


def test_atomic_write_refuses_a_failed_read_stand_in(home, faults):
    path = home / "config.yaml"
    before = path.read_bytes()
    faults.arm(1)
    stand_in = read_raw_config()
    stand_in["x"] = 1
    with pytest.raises(RuntimeError, match="не удалось прочитать"):
        atomic_config_write(path, stand_in)
    assert path.read_bytes() == before


def test_read_error_does_not_copy_the_intact_file_as_corrupt(home, faults, capsys):
    faults.arm(1)
    load_config()
    assert not list(home.glob("config.yaml.corrupt.*"))
    assert "ошибка чтения временная" in capsys.readouterr().err


def test_healthy_load_and_save_still_work(home):
    config = load_config()
    config["display"]["skin"] = "default"
    save_config(config)
    assert yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))["display"]["skin"] == "default"
