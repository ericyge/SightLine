"""POST /api/chat with the default keyword client and with a fake model client."""
import pytest

from app import llm, db
from tests.test_api import frame


class FakeLLM(llm.LLMClient):
    """Returns a canned LLMResult, or raises, and records the messages it was given."""
    provider = "fake"

    def __init__(self, result=None, error=None):
        self.result, self.error, self.messages = result, error, None

    def chat(self, messages, tools):
        self.messages = messages
        if self.error:
            raise self.error
        return self.result


def call(name, **args):
    return llm.LLMResult(tool_calls=[llm.ToolCall(name, args)])


def use(monkeypatch, fake):
    monkeypatch.setattr(llm, "get_llm", lambda: fake)
    return fake


def chat(client, text, **extra):
    r = client.post("/api/chat", json={"text": text, **extra})
    assert r.status_code == 200
    return r.json()


# ---- default (mock / keyword) -----------------------------------------------------------

def test_keyword_find(client):
    remember("phone")
    r = chat(client, "take me to my phone")
    assert r == {"intent": "find", "item": "phone", "room": None,
                 "say": "Looking for your phone.", "provider": "mock"}


def test_keyword_stop_set_room_and_chitchat(client):
    assert chat(client, "stop")["intent"] == "stop"
    r = chat(client, "I'm in the living room")
    assert r["intent"] == "set_room" and r["room"] == "living room"
    r = chat(client, "thanks")
    assert r["intent"] == "chitchat" and r["say"] == "You're welcome."


def test_where_uses_stored_sightings(client, jpeg):
    assert chat(client, "where's my wallet")["say"] == "I don't know where your wallet is yet. I haven't seen it."
    client.post("/api/observe", files=frame(jpeg), data={"room": "bedroom"})
    r = chat(client, "where's my wallet")
    assert r["intent"] == "where" and r["item"] == "wallet"
    assert r["say"].startswith("Last seen at") and "bedroom" in r["say"]   # main's place-aware wording


def test_alias_maps_to_item(client):
    remember("medication")
    r = chat(client, "I can't find my meds")
    assert r["intent"] == "find" and r["item"] == "medication"


def test_any_remembered_object_can_be_searched(client):
    remember("unicorn")
    """main's item matching is open-vocabulary: an object off the watchlist is searched on demand."""
    r = chat(client, "find my unicorn")
    assert (r["intent"], r["item"], r["say"]) == ("find", "unicorn", "Looking for your unicorn.")


def test_unknown_room_asks_a_question(client):
    r = chat(client, "I'm in the garage")
    assert r["intent"] == "chitchat" and "bedroom" in r["say"]


def test_validation(client):
    assert client.post("/api/chat", json={"text": ""}).status_code == 422
    assert client.post("/api/chat", json={"text": "x", "history": [{"role": "robot", "content": "x"}]}).status_code == 422


# ---- with a model ----------------------------------------------------------------------

def test_model_tool_call_is_validated_and_say_comes_from_app(client, monkeypatch):
    remember("wallet")
    result = llm.LLMResult(text="Sure! Your wallet is on Mars.",
                           tool_calls=[llm.ToolCall("find_item", {"item": "my Wallet"})])
    use(monkeypatch, FakeLLM(result))
    r = chat(client, "I need my billfold")
    assert (r["intent"], r["item"], r["provider"]) == ("find", "wallet", "fake")
    assert r["say"] == "Looking for your wallet."


def test_model_where_never_trusts_model_text(client, monkeypatch):
    result = llm.LLMResult(text="It's under the sofa.", tool_calls=[llm.ToolCall("where_is_item", {"item": "keys"})])
    use(monkeypatch, FakeLLM(result))
    assert chat(client, "keys?")["say"] == "I don't know where your keys is yet. I haven't seen it."


def test_model_plain_text_is_chitchat(client, monkeypatch):
    use(monkeypatch, FakeLLM(llm.LLMResult(text="Happy to help.")))
    r = chat(client, "hi")
    assert r["intent"] == "chitchat" and r["say"] == "Happy to help."


def test_model_empty_or_long_text(client, monkeypatch):
    use(monkeypatch, FakeLLM(llm.LLMResult(text="")))
    assert chat(client, "hi")["say"] == "Sorry, I didn't catch that."
    use(monkeypatch, FakeLLM(llm.LLMResult(text="word " * 200)))
    assert len(chat(client, "hi")["say"]) <= 200


def test_model_tool_with_bad_args_asks_a_question(client, monkeypatch):
    use(monkeypatch, FakeLLM(call("find_item")))
    assert chat(client, "find it")["intent"] == "chitchat"
    use(monkeypatch, FakeLLM(call("launch_rocket")))
    assert chat(client, "go")["intent"] == "chitchat"


def test_model_set_room(client, monkeypatch):
    use(monkeypatch, FakeLLM(call("set_room", room="The Kitchen")))
    r = chat(client, "we're cooking")
    assert (r["intent"], r["room"]) == ("set_room", "kitchen")


def test_model_error_falls_back_to_keyword(client, monkeypatch):
    remember("keys")
    use(monkeypatch, FakeLLM(error=llm.LLMError("timeout")))
    r = chat(client, "find my keys")
    assert (r["intent"], r["item"], r["provider"]) == ("find", "keys", "keyword_fallback")


def test_prompt_has_profile_room_and_last_six_turns(client, monkeypatch):
    fake = use(monkeypatch, FakeLLM(llm.LLMResult(text="ok")))
    history = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"turn {i}"} for i in range(10)]
    chat(client, "hello", history=history, current_room="kitchen")
    system, *turns, last = fake.messages
    assert system["role"] == "system" and "wallet" in system["content"] and "in the kitchen" in system["content"]
    assert [t["content"] for t in turns] == [f"turn {i}" for i in range(4, 10)]
    assert last == {"role": "user", "content": "hello"}


def test_chat_event_pushed_and_nothing_stored(client, monkeypatch):
    with client.websocket_connect("/ws/events") as ws:
        chat(client, "stop")
        ev = ws.receive_json()
    assert ev["type"] == "chat" and ev["user_text"] == "stop"
    assert (ev["intent"], ev["provider"], ev["say"]) == ("stop", "mock", "Stopped.")
    from app import db
    with db.connect() as c:
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert not {t for t in tables if "chat" in t or "history" in t}


def test_where_via_chat_does_not_duplicate_say_event(client):
    with client.websocket_connect("/ws/events") as ws:
        chat(client, "where's my wallet")
        assert ws.receive_json()["type"] == "chat"
        chat(client, "stop")           # if a stray "say" event had been queued it would come first
        assert ws.receive_json()["type"] == "chat"


def remember(item):
    db.add_sighting(item, 'kitchen', 'on counter', [.1, .2, .3, .4], None,
                    '2026-10-17T20:00:00Z', '')


@pytest.mark.parametrize('text,item', [('find my wallet', 'wallet'), ('find my stapler', 'stapler'),
                                     ("I can't find my meds", 'medication')])
def test_find_unseen_object_answers_unknown_without_starting_guidance(client, text, item):
    r = chat(client, text)
    assert r['intent'] == 'where' and r['item'] == item
    assert r['say'] == f"I don't know where your {item} is yet. I haven't seen it."
    remember(item)
    assert chat(client, text)['intent'] == 'find'


def test_model_cannot_start_guidance_to_unseen_object(client, monkeypatch):
    use(monkeypatch, FakeLLM(llm.LLMResult(text='Walk to the kitchen.',
        tool_calls=[llm.ToolCall('find_item', {'item': 'wallet'})])))
    r = chat(client, 'take me to it')
    assert r['intent'] == 'where' and "don't know where" in r['say']
    assert 'kitchen' not in r['say']
