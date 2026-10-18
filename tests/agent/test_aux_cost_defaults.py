"""Cost-aware aux defaults (18.10): titles on the fast tier with a current
Anthropic fast model; background review bounded to 150K input / 6 rounds."""

from korra_cli.config import DEFAULT_CONFIG


def test_title_generation_prefers_fast_model_by_default():
    assert DEFAULT_CONFIG["auxiliary"]["title_generation"]["prefer_fast_model"] is True


def test_title_generation_opts_into_fast_model_routing(monkeypatch):
    from agent import auxiliary_client as ac

    monkeypatch.setattr(
        ac, "_get_auxiliary_task_config",
        lambda task: dict(DEFAULT_CONFIG["auxiliary"].get(task) or {}),
    )
    assert ac._task_prefers_fast_model("title_generation") is True
    # "auto = main model" stays true for every other aux task.
    assert ac._task_prefers_fast_model("compression") is False


def test_anthropic_fast_model_is_current_haiku():
    from agent import auxiliary_client as ac

    assert ac._API_KEY_PROVIDER_AUX_MODELS_FALLBACK["anthropic"] == "claude-haiku-5-5"
    assert "20251001" not in ac._API_KEY_PROVIDER_AUX_MODELS_FALLBACK["anthropic"]


def test_background_review_budget_defaults():
    from agent import background_review as br

    assert DEFAULT_CONFIG["auxiliary"]["background_review"]["max_input_tokens"] == 150_000
    assert br._REVIEW_MAX_INPUT_TOKENS_DEFAULT == 150_000
    assert br._review_input_token_budget({}) == 150_000
    assert br._REVIEW_MAX_ITERATIONS == 6
    # Explicit config still wins; <=0 disables the cap.
    assert br._review_input_token_budget({"max_input_tokens": 400_000}) == 400_000
    assert br._review_input_token_budget({"max_input_tokens": 0}) is None
