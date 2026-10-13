"""Docker fixture teardown must not leave anonymous volumes behind."""
from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace
from uuid import uuid4

import pytest

from tests.docker.conftest import container_name as container_name_fixture


@pytest.mark.parametrize("body_fails", [False, True])
def test_teardown_removes_anonymous_volume_and_preserves_named_volume(
    built_image: str, body_fails: bool,
) -> None:
    def docker(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["docker", *args], capture_output=True, text=True,
            timeout=30, check=check,
        )

    request = SimpleNamespace(node=SimpleNamespace(name=f"cleanup-{uuid4().hex}"))
    teardown = container_name_fixture.__wrapped__(request)
    name = next(teardown)
    named_volume = f"{name}-named"
    anonymous_volumes: list[str] = []
    try:
        docker("volume", "create", named_volume)
        # Create only: no application, gateway or network is started. /opt/data
        # is an anonymous volume declared by the image; the second is named.
        docker(
            "create", "--network", "none", "--entrypoint", "/bin/true",
            "--name", name, "--mount",
            f"type=volume,src={named_volume},dst=/cleanup-named", built_image,
        )
        container = json.loads(docker("inspect", name).stdout)[0]
        anonymous_volumes = [
            mount["Name"] for mount in container["Mounts"]
            if mount["Type"] == "volume" and mount["Name"] != named_volume
        ]
        assert anonymous_volumes, "the test image must declare an anonymous volume"

        def finish_body() -> None:
            try:
                if body_fails:
                    raise RuntimeError("simulated test body failure")
            finally:
                # Pytest resumes yield fixtures on both pass and failure.
                with pytest.raises(StopIteration):
                    next(teardown)

        if body_fails:
            with pytest.raises(RuntimeError, match="simulated test body failure"):
                finish_body()
        else:
            finish_body()

        assert docker("inspect", name, check=False).returncode != 0
        for volume in anonymous_volumes:
            assert docker("volume", "inspect", volume, check=False).returncode != 0
        assert docker("volume", "inspect", named_volume, check=False).returncode == 0
    finally:
        # Also clean up when this regression test runs against the buggy code.
        teardown.close()
        docker("rm", "-f", "--volumes", name, check=False)
        for volume in [*anonymous_volumes, named_volume]:
            docker("volume", "rm", volume, check=False)
