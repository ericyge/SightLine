"""LLM clients with no real network: KeywordLLM, mocked HTTP for the OpenAI-style clients, fallback."""
import json
import logging

import httpx
import pytest

from app import llm

USER = lambda text: [{"role": "system", "content": "sys"}, {"role": "user", "content": text}]  # noqa: E731
SECRET = "sk-test-SECRET-123"
GEMINI_SECRET = "gm-test-SECRET-456"


def tool_reply(name, args, as_string=True, key="tool_calls"):
    fn_args = json.dumps(args) if as_string else args
    return {"choices": [{"message": {"content": None, key: [
        {"id": "1", "type": "function", "function": {"name": name, "arguments": fn_args}}]}}]}


def text_reply(text):
    return {"choices": [{"message": {"content": text}}]}


def transport(handler):
    return httpx.MockTransport(handler)


@pytest.fixture()
def compat_env(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.example/v1/")
    monkeypatch.setenv("LLM_API_KEY", SECRET)
    monkeypatch.setenv("LLM_MODEL", "some-model")


@pytest.fixture()
def gemini_env(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", GEMINI_SECRET)
    monkeypatch.setenv("GEMINI_MODEL", "gemini-example-flash")


@pytest.fixture()
def purple_env(monkeypatch):
    monkeypatch.delenv("PURPLE_API_BASE", raising=False)
    monkeypatch.setenv("PURPLE_API_TOKEN", SECRET)
    monkeypatch.setenv("PURPLE_MODEL", "purple-model-x")


# ---- KeywordLLM -------------------------------------------------------------------------

@pytest.mark.parametrize("text,tool,args", [
    ("where's my wallet", "where_is_item", {"item": "wallet"}),
    ("Where is my phone?", "where_is_item", {"item": "phone"}),
    ("have you seen my glasses", "where_is_item", {"item": "glasses"}),
    ("I can't find my keys", "find_item", {"item": "keys"}),
    ("take me to my phone", "find_item", {"item": "phone"}),
    ("find the medication please", "find_item", {"item": "medication"}),
    ("stop", "stop_guidance", {}),
    ("never mind", "stop_guidance", {}),
    ("i got the wallet", "stop_guidance", {}),
    ("i got my wallet", "stop_guidance", {}),
    ("got the keys", "stop_guidance", {}),
    ("i found the phone", "stop_guidance", {}),
    ("I'm in the kitchen", "set_room", {"room": "kitchen"}),
])
def test_keyword_tool_calls(text, tool, args):
    r = llm.KeywordLLM().chat(USER(text), llm.TOOLS)
    assert [(c.name, c.arguments) for c in r.tool_calls] == [(tool, args)]


@pytest.mark.parametrize("text,expected", [("thanks", "You're welcome."), ("hello there", llm.HELP_TEXT)])
def test_keyword_chitchat(text, expected):
    r = llm.KeywordLLM().chat(USER(text), llm.TOOLS)
    assert r.tool_calls == [] and r.text == expected


def test_keyword_uses_latest_user_message():
    msgs = [{"role": "user", "content": "stop"}, {"role": "assistant", "content": "ok"},
            {"role": "user", "content": "where's my wallet"}]
    assert llm.KeywordLLM().chat(msgs, llm.TOOLS).tool_calls[0].name == "where_is_item"


# ---- OpenAI-compatible client -----------------------------------------------------------

def test_openai_compat_tool_call(compat_env):
    seen = {}

    def handler(request):
        seen["url"], seen["auth"], seen["body"] = str(request.url), request.headers["authorization"], json.loads(request.content)
        return httpx.Response(200, json=tool_reply("find_item", {"item": "wallet"}))

    c = llm.OpenAICompatLLM(transport=transport(handler))
    r = c.chat(USER("find my wallet"), llm.TOOLS)
    assert r.tool_calls[0].name == "find_item" and r.tool_calls[0].arguments == {"item": "wallet"}
    assert seen["url"] == "https://llm.example/v1/chat/completions"
    assert seen["auth"] == f"Bearer {SECRET}"
    assert seen["body"]["model"] == "some-model" and seen["body"]["tools"] == llm.TOOLS
    assert seen["body"]["messages"][1]["content"] == "find my wallet"
    assert SECRET not in json.dumps(seen["body"])


def test_openai_compat_plain_text_and_dict_arguments(compat_env):
    c = llm.OpenAICompatLLM(transport=transport(lambda r: httpx.Response(200, json=text_reply("Hi there"))))
    r = c.chat(USER("hello"), llm.TOOLS)
    assert r.text == "Hi there" and r.tool_calls == []
    c = llm.OpenAICompatLLM(transport=transport(
        lambda r: httpx.Response(200, json=tool_reply("set_room", {"room": "bedroom"}, as_string=False))))
    assert c.chat(USER("x"), llm.TOOLS).tool_calls[0].arguments == {"room": "bedroom"}


def test_openai_compat_requires_all_settings(monkeypatch):
    for k in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(llm.LLMError, match="must all be set"):
        llm.OpenAICompatLLM().chat(USER("x"), llm.TOOLS)


# ---- Purple client ----------------------------------------------------------------------

def test_purple_default_base_auth_and_model(purple_env):
    seen = {}

    def handler(request):
        seen["url"], seen["auth"], seen["body"] = str(request.url), request.headers["authorization"], json.loads(request.content)
        return httpx.Response(200, json=tool_reply("stop_guidance", {}))

    r = llm.PurpleLLM(transport=transport(handler)).chat(USER("stop"), llm.TOOLS)
    assert r.tool_calls[0].name == "stop_guidance"
    assert seen["url"] == "https://purple.uw.edu/api/external/v1/chat/completions"
    assert seen["auth"] == f"Bearer {SECRET}"
    assert seen["body"]["model"] == "purple-model-x"


def test_purple_base_override_and_camelcase_tool_calls(purple_env, monkeypatch):
    monkeypatch.setenv("PURPLE_API_BASE", "https://example.test/v9/")
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, json=tool_reply("find_item", {"item": "keys"}, key="toolCalls"))

    r = llm.PurpleLLM(transport=transport(handler)).chat(USER("x"), llm.TOOLS)
    assert seen["url"] == "https://example.test/v9/chat/completions"
    assert r.tool_calls[0].arguments == {"item": "keys"}


def test_purple_field_names_mapping(purple_env, monkeypatch):
    monkeypatch.setattr(llm.PurpleLLM, "FIELD_NAMES", {"temperature": "temp"})
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=text_reply("ok"))

    llm.PurpleLLM(transport=transport(handler)).chat(USER("x"), llm.TOOLS)
    assert "temp" in seen["body"] and "temperature" not in seen["body"]


def test_purple_needs_token_and_model(monkeypatch):
    monkeypatch.delenv("PURPLE_API_TOKEN", raising=False)
    monkeypatch.delenv("PURPLE_MODEL", raising=False)
    with pytest.raises(llm.LLMError, match="PURPLE_API_TOKEN"):
        llm.PurpleLLM().chat(USER("x"), llm.TOOLS)
    monkeypatch.setenv("PURPLE_API_TOKEN", SECRET)
    with pytest.raises(llm.LLMError, match="PURPLE_MODEL"):
        llm.PurpleLLM().chat(USER("x"), llm.TOOLS)


def test_list_models(purple_env):
    c = llm.PurpleLLM(transport=transport(lambda r: httpx.Response(200, json={"data": [{"id": "a"}, {"id": "b"}]})))
    assert c.list_models() == ["a", "b"]


# ---- selection + fallback ---------------------------------------------------------------

def test_get_llm_selection(monkeypatch):
    for value, cls in (("mock", llm.KeywordLLM), ("purple", llm.PurpleLLM),
                       ("gemini", llm.GeminiLLM), ("openai_compat", llm.OpenAICompatLLM),
                       ("bogus", llm.KeywordLLM)):
        monkeypatch.setenv("LLM_PROVIDER", value)
        assert isinstance(llm.get_llm(), cls)
    monkeypatch.delenv("LLM_PROVIDER")
    assert llm.get_llm().provider == "mock"


def use_client(monkeypatch, client):
    monkeypatch.setattr(llm, "get_llm", lambda: client)


def test_fallback_not_used_on_success(monkeypatch, compat_env):
    use_client(monkeypatch, llm.OpenAICompatLLM(transport=transport(
        lambda r: httpx.Response(200, json=tool_reply("stop_guidance", {})))))
    result, provider = llm.chat_with_fallback(USER("anything"))
    assert provider == "openai_compat" and result.tool_calls[0].name == "stop_guidance"


@pytest.mark.parametrize("handler", [
    lambda r: httpx.Response(401, json={"error": "bad token"}),
    lambda r: httpx.Response(500, text="boom"),
    lambda r: (_ for _ in ()).throw(httpx.ReadTimeout("slow")),
    lambda r: httpx.Response(200, text="not json"),
    lambda r: httpx.Response(200, json={"unexpected": True}),
], ids=["401", "500", "timeout", "bad-json", "bad-shape"])
def test_failures_fall_back_to_keyword(monkeypatch, compat_env, handler):
    use_client(monkeypatch, llm.OpenAICompatLLM(transport=transport(handler)))
    result, provider = llm.chat_with_fallback(USER("where's my wallet"))
    assert provider == "keyword_fallback"
    assert result.tool_calls[0].name == "where_is_item"


def test_missing_config_falls_back(monkeypatch):
    monkeypatch.delenv("PURPLE_API_TOKEN", raising=False)
    use_client(monkeypatch, llm.PurpleLLM())
    assert llm.chat_with_fallback(USER("stop"))[1] == "keyword_fallback"


def test_failure_logs_never_contain_token_or_prompt(monkeypatch, compat_env, caplog):
    use_client(monkeypatch, llm.OpenAICompatLLM(transport=transport(lambda r: httpx.Response(401))))
    with caplog.at_level(logging.DEBUG):
        llm.chat_with_fallback(USER("my secret sentence"))
    assert SECRET not in caplog.text and "my secret sentence" not in caplog.text
    assert "HTTP 401" in caplog.text


def test_system_prompt_contains_profile():
    items = [{"name": "phone", "aliases": ["cell phone"]}, {"name": "keys", "aliases": []}]
    p = llm.build_system_prompt(items, ["bedroom", "kitchen"], "kitchen")
    assert "phone (also: cell phone)" in p and "keys" in p and "bedroom, kitchen" in p
    assert "in the kitchen" in p and "under 15 words" in p
