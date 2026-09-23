"""web_extract must not inherit a search-only backend (field fix, 22.09.2026).

With DDGS installed and no web backend configured, the shared autodetect
picked ``ddgs`` for every capability, and ``web_extract`` failed at once:
DDGS only searches. Resolution is now per capability.
"""

import pytest

import tools.web_tools as web_tools
from agent import web_search_registry as registry
from agent.web_search_provider import WebSearchProvider


class _Fake(WebSearchProvider):
    def __init__(self, name, *, search, extract):
        self._name, self._search, self._extract = name, search, extract

    @property
    def name(self):
        return self._name

    def is_available(self):
        return True

    def supports_search(self):
        return self._search

    def supports_extract(self):
        return self._extract


@pytest.fixture
def providers(monkeypatch):
    registry._reset_for_tests()
    registry.register_provider(_Fake("ddgs", search=True, extract=False))
    registry.register_provider(_Fake("parallel", search=True, extract=True))
    monkeypatch.setattr(web_tools, "_ensure_web_plugins_loaded", lambda: None)
    monkeypatch.setattr(registry, "_read_config_key", lambda *keys: None)
    monkeypatch.setattr(web_tools, "_get_backend", lambda: "ddgs")
    yield
    registry._reset_for_tests()


def test_extract_picks_an_extract_capable_provider(providers, monkeypatch):
    monkeypatch.setattr(web_tools, "_load_web_config", dict)
    assert web_tools._get_capability_backend("extract") == "parallel"


def test_explicit_backends_are_kept(providers, monkeypatch):
    monkeypatch.setattr(web_tools, "_load_web_config", lambda: {"extract_backend": "firecrawl"})
    assert web_tools._get_capability_backend("extract") == "firecrawl"
    monkeypatch.setattr(web_tools, "_load_web_config", lambda: {"backend": "ddgs"})
    assert web_tools._get_capability_backend("extract") == "ddgs"
