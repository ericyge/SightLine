"""Gemini (OpenAI-compatible endpoint) and the purple -> gemini -> keyword chain. No real network."""
import json
import logging

import httpx
import pytest

from app import llm
from tests.test_llm import GEMINI_SECRET, SECRET, USER, text_reply, tool_reply

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"


@pytest.fixture()
def gemini_env(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", GEMINI_SECRET)
    monkeypatch.setenv("GEMINI_MODEL", "gemini-example-flash")


# ---- Gemini -----------------------------------------------------------------------------

def test_gemini_tool_call_documented_url_and_bearer_auth(gemini_env):
    seen = {}

    def handler(request):
        seen["url"], seen["auth"] = str(request.url), request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=tool_reply("where_is_item", {"item": "keys"}))

    r = llm.GeminiLLM(transport=httpx.MockTransport(handler)).chat(USER("keys?"), llm.TOOLS)
    assert r.tool_calls[0].name == "where_is_item" and r.tool_calls[0].arguments == {"item": "keys"}
    assert seen["url"] == GEMINI_URL
    assert seen["auth"] == f"Bearer {GEMINI_SECRET}"
    assert seen["body"]["model"] == "gemini-example-flash" and seen["body"]["tools"] == llm.TOOLS
    assert GEMINI_SECRET not in json.dumps(seen["body"])


def test_gemini_plain_text_and_base_override(gemini_env, monkeypatch):
    monkeypatch.setenv("GEMINI_BASE_URL", "https://proxy.test/openai/")
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, json=text_reply("Hello!"))

    r = llm.GeminiLLM(transport=httpx.MockTransport(handler)).chat(USER("hi"), llm.TOOLS)
    assert r.text == "Hello!" and r.tool_calls == []
    assert seen["url"] == "https://proxy.test/openai/chat/completions"


def test_blank_base_url_and_timeout_use_defaults(gemini_env, monkeypatch):
    """.env.example ships these blank; a blank value must not break the request."""
    monkeypatch.setenv("GEMINI_BASE_URL", "")
    monkeypatch.setenv("LLM_TIMEOUT_S", "")
    monkeypatch.setenv("PURPLE_API_BASE", "")
    seen = []
    c = llm.GeminiLLM(transport=httpx.MockTransport(
        lambda r: seen.append(str(r.url)) or httpx.Response(200, json=text_reply("ok"))))
    assert c.chat(USER("x"), llm.TOOLS).text == "ok" and seen == [GEMINI_URL]
    monkeypatch.setenv("PURPLE_API_TOKEN", SECRET)
    monkeypatch.setenv("PURPLE_MODEL", "p")
    assert llm.PurpleLLM()._settings()[0] == llm.DEFAULT_PURPLE_BASE


def test_gemini_needs_key_and_model(monkeypatch):
    with pytest.raises(llm.LLMNotConfigured, match="GEMINI_API_KEY"):
        llm.GeminiLLM().chat(USER("x"), llm.TOOLS)
    monkeypatch.setenv("GEMINI_API_KEY", GEMINI_SECRET)
    with pytest.raises(llm.LLMNotConfigured, match="GEMINI_MODEL"):
        llm.GeminiLLM().chat(USER("x"), llm.TOOLS)


# ---- chain ------------------------------------------------------------------------------

class Calls:
    """A mock transport that counts requests."""

    def __init__(self, handler):
        self.n, self.handler = 0, handler
        self.transport = httpx.MockTransport(self._handle)

    def _handle(self, request):
        self.n += 1
        return self.handler(request)


def ok_stop(request):
    return httpx.Response(200, json=tool_reply("stop_guidance", {}))


def fail_500(request):
    return httpx.Response(500)


def fail_401(request):
    return httpx.Response(401)


def fail_timeout(request):
    raise httpx.ReadTimeout("slow")


def chain(monkeypatch, provider, purple=None, gemini=None):
    """Select a provider, configure both keys, and inject mock transports."""
    monkeypatch.setenv("LLM_PROVIDER", provider)
    monkeypatch.setenv("PURPLE_API_TOKEN", SECRET)
    monkeypatch.setenv("PURPLE_MODEL", "p")
    monkeypatch.setenv("GEMINI_API_KEY", GEMINI_SECRET)
    monkeypatch.setenv("GEMINI_MODEL", "g")
    clients = {"purple": llm.PurpleLLM(transport=purple.transport if purple else None),
               "gemini": llm.GeminiLLM(transport=gemini.transport if gemini else None)}
    monkeypatch.setattr(llm, "_make", lambda name: clients[name])


def test_purple_answers_first(monkeypatch):
    p, g = Calls(ok_stop), Calls(ok_stop)
    chain(monkeypatch, "purple", p, g)
    assert llm.chat_with_fallback(USER("x"))[1] == "purple"
    assert (p.n, g.n) == (1, 0)


@pytest.mark.parametrize("purple_handler", [fail_500, fail_timeout, fail_401], ids=["500", "timeout", "401"])
def test_purple_fails_gemini_answers(monkeypatch, purple_handler):
    p, g = Calls(purple_handler), Calls(ok_stop)
    chain(monkeypatch, "purple", p, g)
    result, provider = llm.chat_with_fallback(USER("x"))
    assert provider == "gemini" and result.tool_calls[0].name == "stop_guidance"
    assert (p.n, g.n) == (1, 1)


def test_both_fail_ends_at_keyword(monkeypatch):
    chain(monkeypatch, "purple", Calls(fail_500), Calls(fail_timeout))
    result, provider = llm.chat_with_fallback(USER("where's my wallet"))
    assert provider == "keyword_fallback" and result.tool_calls[0].name == "where_is_item"


def test_gemini_selected_fails_to_keyword_without_trying_purple(monkeypatch):
    p, g = Calls(ok_stop), Calls(fail_500)
    chain(monkeypatch, "gemini", p, g)
    assert llm.chat_with_fallback(USER("stop"))[1] == "keyword_fallback"
    assert p.n == 0 and g.n == 1


def test_unconfigured_providers_are_skipped(monkeypatch):
    g = Calls(ok_stop)
    chain(monkeypatch, "purple", None, g)
    monkeypatch.delenv("PURPLE_API_TOKEN")                  # purple has no token -> skipped
    assert llm.chat_with_fallback(USER("x"))[1] == "gemini" and g.n == 1
    monkeypatch.delenv("GEMINI_API_KEY")                    # gemini has no key either
    result, provider = llm.chat_with_fallback(USER("stop"))
    assert provider == "keyword_fallback" and result.tool_calls[0].name == "stop_guidance"
    assert g.n == 1                                         # nothing was sent


def test_mock_never_uses_network(monkeypatch):
    p, g = Calls(ok_stop), Calls(ok_stop)
    chain(monkeypatch, "mock", p, g)
    assert llm.chat_with_fallback(USER("stop"))[1] == "mock"
    assert (p.n, g.n) == (0, 0)


def test_logs_never_contain_keys_headers_or_prompt(monkeypatch, caplog):
    chain(monkeypatch, "purple", Calls(fail_401), Calls(fail_500))
    with caplog.at_level(logging.DEBUG):
        llm.chat_with_fallback(USER("my private sentence"))
    for secret in (SECRET, GEMINI_SECRET, "my private sentence", "Bearer"):
        assert secret not in caplog.text
