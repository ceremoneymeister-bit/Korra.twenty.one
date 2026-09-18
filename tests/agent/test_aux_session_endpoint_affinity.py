"""Session credentials are recoverable only at their original trust boundary."""

from types import SimpleNamespace

import pytest

from agent import auxiliary_client as aux


SESSION = {
    "provider": "openai-api",
    "model": "gpt-5.4",
    "base_url": "https://proxy.example:8443/v1",
    "api_key": "session-key",
}


@pytest.mark.parametrize(
    "rejecting_base",
    [
        "https://api.openai.com/v1/",
        "https://proxy.example:9443/v1/",
        "http://proxy.example:8443/v1/",
    ],
)
def test_session_key_rejected_at_foreign_origin_is_not_rotated(rejecting_base):
    client = SimpleNamespace(base_url=rejecting_base, api_key="session-key")
    assert aux._recoverable_pool_provider(
        "openai-api",
        client,
        main_runtime=SESSION,
    ) is None


def test_rotation_survives_at_session_origin_and_for_independent_aux_pool():
    same_origin = SimpleNamespace(
        base_url="https://proxy.example:8443/v1/",
        api_key="session-key",
    )
    assert aux._recoverable_pool_provider(
        "openai-api",
        same_origin,
        main_runtime=SESSION,
    ) == "openai-api"

    independent = SimpleNamespace(
        base_url="https://api.openai.com/v1/",
        api_key="auxiliary-pool-key",
    )
    assert aux._recoverable_pool_provider(
        "openai-api",
        independent,
        main_runtime=SESSION,
    ) == "openai-api"
