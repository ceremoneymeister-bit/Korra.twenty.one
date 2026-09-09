from html.parser import HTMLParser
import pytest
from korra_cli.session_export_html import generate_multi_session_html_export


class Document(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.tags = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


@pytest.fixture
def export():
    return generate_multi_session_html_export([{
        "id": "one", "title": "Hermes <script>attack()</script>",
        "messages": [{"role": "assistant", "content": "Hermes <b>literal</b>",
                      "reasoning": "private synthetic reason",
                      "tool_calls": [{"function": {"name": "<img src=x>", "arguments": "{}"}}]}],
        "system_prompt": "Synthetic instruction",
    }, {"id": "two", "messages": []}])


def test_export_document_is_russian_offline_and_light(export):
    doc = Document(export)
    assert ("html", {"lang": "ru"}) in doc.tags
    assert not any(tag == "link" and attrs.get("href", "").startswith("https:")
                   for tag, attrs in doc.tags)
    assert "prefers-color-scheme" not in export
    assert 'img-src data:' in export
    assert "font-src https:" not in export
    assert any(tag == "script" and attrs.get("nonce") for tag, attrs in doc.tags)


def test_all_disclosures_are_native_and_named(export):
    doc = Document(export)
    summaries = [attrs for tag, attrs in doc.tags if tag == "summary"]
    assert len(summaries) == 4
    assert len([1 for tag, _ in doc.tags if tag == "details"]) == len(summaries)


def test_export_preserves_user_brand_mentions_and_escapes_markup(export):
    assert "Hermes &lt;b&gt;literal&lt;/b&gt;" in export
    assert "Hermes &lt;script&gt;attack()&lt;/script&gt;" in export
    assert "<script>attack()" not in export
    assert "<img src=x>" not in export


def test_empty_export_is_russian():
    assert ("html", {"lang": "ru"}) in Document(generate_multi_session_html_export([])).tags
