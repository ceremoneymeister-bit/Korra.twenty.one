"""Runtime-sensitive availability must not alias another model or profile."""
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from agent import auxiliary_client as aux
from korra_constants import set_hermes_home_override, reset_hermes_home_override
from tools.registry import registry, ToolRegistry, invalidate_check_fn_cache
import model_tools


@pytest.fixture(autouse=True)
def clear_caches():
    aux.clear_runtime_main()
    invalidate_check_fn_cache()
    model_tools._clear_tool_defs_cache()
    yield
    aux.clear_runtime_main()
    invalidate_check_fn_cache()
    model_tools._clear_tool_defs_cache()


@pytest.mark.parametrize('order', [(False, True, False), (True, False, True)])
def test_real_vision_gate_and_both_definition_caches(tmp_path, order):
    from tools.vision_tools import check_vision_requirements
    home = tmp_path / 'vision-profile'
    home.mkdir()
    (home / 'config.yaml').write_text("model:\n  provider: ''\n  default: ''\n")
    (home / 'auth.json').write_text(json.dumps({'active_provider': 'openai-codex', 'providers': {
        'openai-codex': {'tokens': {'access_token': 'synthetic-access', 'refresh_token': 'synthetic-refresh'}}}}))
    token = set_hermes_home_override(str(home))
    try:
        for enabled in order:
            runtime = {'provider': 'openai-codex', 'model': 'gpt-4o'} if enabled else {}
            with aux.scoped_runtime_main(runtime):
                assert check_vision_requirements() is enabled
                direct = registry.get_definitions({'vision_analyze'}, quiet=True)
                assert bool(direct) is enabled
                outer = model_tools.get_tool_definitions(['vision'], quiet_mode=True, skip_tool_search_assembly=True)
                assert ('vision_analyze' in {item['function']['name'] for item in outer}) is enabled
    finally:
        reset_hermes_home_override(token)


def test_concurrent_model_and_profile_gates_are_isolated(tmp_path):
    from threading import Barrier
    reg = ToolRegistry()
    gate = lambda: aux._runtime_main_value('model') == 'with-vision'
    reg.register(name='test_vision', toolset='vision', handler=lambda *a: '{}',
                 schema={'name': 'test_vision', 'parameters': {'type': 'object', 'properties': {}}}, check_fn=gate)
    barrier = Barrier(2)
    def probe(profile, model):
        token = set_hermes_home_override(str(tmp_path / profile))
        try:
            with aux.scoped_runtime_main({'provider': 'test', 'model': model}):
                barrier.wait(timeout=5)
                return bool(reg.get_definitions({'test_vision'}, quiet=True))
        finally:
            reset_hermes_home_override(token)
    for profiles in [('same', 'same'), ('one', 'two')]:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(probe, profiles[0], 'with-vision')
            second = pool.submit(probe, profiles[1], 'without-vision')
            assert first.result() is True and second.result() is False


def test_agent_initial_snapshot_uses_resolved_runtime(monkeypatch):
    import run_agent
    from run_agent import AIAgent
    snapshots = []
    def definitions(**kwargs):
        snapshots.append((aux._runtime_main_value('provider'), aux._runtime_main_value('model')))
        return [{'type': 'function', 'function': {'name': 'vision_analyze', 'parameters': {'type': 'object', 'properties': {}}}}]
    monkeypatch.setattr(run_agent, 'get_tool_definitions', definitions)
    # Construction only, no chat/model request. OpenAI SDK client is local.
    with aux.scoped_runtime_main({'provider': 'other', 'model': 'other-model'}):
        agent = AIAgent(provider='custom', model='resolved-vision-model',
                        api_key='synthetic-key', base_url='http://127.0.0.1:9/v1',
                        quiet_mode=True, skip_context_files=True, skip_memory=True)
        assert snapshots == [('custom', 'resolved-vision-model')]
        assert agent.valid_tool_names == {item['function']['name'] for item in agent.tools}
        assert aux._runtime_main_value('model') == 'other-model'
        assert agent.tools[0]['function']['name'] == 'vision_analyze'
