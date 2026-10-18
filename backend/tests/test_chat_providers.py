"""/api/chat with external providers: what is sent, and which provider is reported."""
import json

import httpx

from app import llm
from tests.test_api import frame
from tests.test_chat_api import chat


def test_gemini_request_has_only_allowed_data(client, monkeypatch, jpeg):
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        args = json.dumps({"item": "wallet"})
        return httpx.Response(200, json={"choices": [{"message": {"tool_calls": [
            {"function": {"name": "where_is_item", "arguments": args}}]}}]})

    monkeypatch.setenv("GEMINI_API_KEY", "gm-key")
    monkeypatch.setenv("GEMINI_MODEL", "m")
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setattr(llm, "_make", lambda name: llm.GeminiLLM(transport=httpx.MockTransport(handler)))
    client.post("/api/observe", files=frame(jpeg), data={"room": "bedroom"})   # makes sightings + thumbs
    r = chat(client, "where did I leave it", history=[{"role": "user", "content": "hi"}],
             current_room="kitchen")
    assert r["provider"] == "gemini" and r["intent"] == "where"
    assert r["say"].startswith("Last seen at")              # the answer came from stored sightings
    body = json.dumps(sent[0])
    assert set(sent[0]) == {"model", "messages", "tools", "temperature"}
    assert [m["role"] for m in sent[0]["messages"]] == ["system", "user", "user"]
    for forbidden in ("data:image", "/api/thumbs", "thumb", "home_spot", "a smartphone", "on the desk",
                      "next to the lamp", "confidence", "bedroom desk", "gm-key"):
        assert forbidden not in body, forbidden
    for allowed in ("wallet", "cell phone", "living room", "kitchen"):   # names, aliases, rooms
        assert allowed in body


def test_endpoint_reports_the_provider_that_answered(client, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "purple")
    for name, value in (("PURPLE_API_TOKEN", "t"), ("PURPLE_MODEL", "m"),
                        ("GEMINI_API_KEY", "g"), ("GEMINI_MODEL", "m")):
        monkeypatch.setenv(name, value)
    ok = httpx.MockTransport(lambda r: httpx.Response(200, json={"choices": [{"message": {"content": "Hi!"}}]}))
    bad = httpx.MockTransport(lambda r: httpx.Response(500))
    clients = {"purple": llm.PurpleLLM(transport=bad), "gemini": llm.GeminiLLM(transport=ok)}
    monkeypatch.setattr(llm, "_make", lambda name: clients[name])
    assert chat(client, "hello")["provider"] == "gemini"
    clients["gemini"] = llm.GeminiLLM(transport=bad)
    assert chat(client, "stop")["provider"] == "keyword_fallback"
