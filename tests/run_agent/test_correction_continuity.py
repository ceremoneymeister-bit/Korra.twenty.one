"""Offline continuity contract: preserve obligations and the cached prefix.

The scripted provider witnesses the actual requests and delivers a corrected
artifact. This tests plumbing, not whether a live model obeys the guidance.
"""
import copy
from unittest.mock import MagicMock

from tests.run_agent.test_run_agent import _mock_response


def test_correction_and_side_question_keep_original_result_and_system_prefix(tmp_path, monkeypatch):
    from run_agent import AIAgent

    monkeypatch.setattr('run_agent.OpenAI', MagicMock())
    monkeypatch.setattr('run_agent.get_tool_definitions', lambda *args, **kwargs: [{'type': 'function', 'function': {'name': 'read_file', 'parameters': {'type': 'object', 'properties': {}}}}])
    monkeypatch.setattr('run_agent.check_toolset_requirements', lambda **_: {})
    agent = AIAgent(model='openai/gpt-5.5', api_key='test-key', base_url='http://localhost:8080/v1',
                    quiet_mode=True, skip_memory=True, skip_context_files=True, api_mode="chat_completions")
    agent.client = MagicMock()
    agent._disable_streaming = True
    agent.compression_enabled = False
    agent._execution_guidance = True
    requests = []
    artifact = tmp_path / 'comparison.md'
    original = 'Составь таблицу стоимости: 2 товара по 10 рублей.'
    correction = 'Поправка: второй товар стоит 20 рублей. Кстати, что значит НДС?'

    def respond(**kwargs):
        messages = copy.deepcopy(kwargs['messages'])
        requests.append(messages)
        system = next(m['content'] for m in messages if m['role'] in ('system', 'developer'))
        assert "A correction changes the method" in system
        assert "Side requests add work" in system
        assert "every outstanding requested result is delivered and verified" in system
        if len(requests) == 1:
            assert original in messages[-1]['content']
            return _mock_response(content='Проверяю исходные цены.')
        assert any(original in str(m.get('content')) for m in messages if m['role'] == 'user')
        assert correction in messages[-1]['content']
        assert system == next(m['content'] for m in requests[0] if m['role'] in ('system', 'developer'))
        artifact.write_text('| Товар | Цена |\n| 1 | 10 ₽ |\n| 2 | 20 ₽ |\nИтого: 30 ₽.\n')
        return _mock_response(content='НДС — налог на добавленную стоимость.\n' + artifact.read_text())

    agent.client.chat.completions.create.side_effect = respond
    first = agent.run_conversation(original)
    second = agent.run_conversation(correction, conversation_history=first['messages'])
    assert len(requests) == 2
    assert artifact.read_text().strip() in second['final_response']
    assert 'НДС' in second['final_response'] and '30 ₽' in second['final_response']
    assert sum(m['role'] in ('system', 'developer') for m in requests[-1]) == 1
    roles = [m['role'] for m in requests[-1] if m['role'] not in ('system', 'developer')]
    assert all(a != b for a, b in zip(roles, roles[1:]))
