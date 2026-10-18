"""Swappable LLM client for the conversational layer (POST /api/chat).

Providers (env LLM_PROVIDER): mock (default, KeywordLLM, no network) | purple | gemini | openai_compat.
Preference order is purple -> gemini -> keyword: the chain starts at the selected provider and
falls through the later ones on any error, timeout or missing key, always ending at KeywordLLM.
The `provider` reported is whichever one answered ("keyword_fallback" if the chain ran out).

Privacy for every external provider: only the user's transcribed text, the last 6 turns, and
watchlist names/aliases/room names are sent (see build_system_prompt and the /api/chat route).
Never frames, thumbnails or sighting history. Never log tokens, Authorization headers, request
bodies or model replies.
"""
import json
import logging
import os
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import httpx

from .net import VERIFY as _VERIFY

log = logging.getLogger("sightline.llm")

DEFAULT_PURPLE_BASE = "https://purple.uw.edu/api/external/v1"
DEFAULT_TIMEOUT_S = 3.0
# From Google's "OpenAI compatibility" page (ai.google.dev/gemini-api/docs/openai). GEMINI_BASE_URL
# overrides it in case Google moves the endpoint.
DEFAULT_GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/openai"
PROVIDER_ORDER = ["purple", "gemini"]   # preference order; keyword is always the last resort


class LLMError(Exception):
    """Any failure talking to a model (config, network, HTTP status, bad response shape)."""


class LLMNotConfigured(LLMError):
    """A provider's env settings are missing; the chain skips it quietly."""


@dataclass
class ToolCall:
    name: str
    arguments: dict


@dataclass
class LLMResult:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)


def _tool(name: str, description: str, props: dict | None = None) -> dict:
    """One OpenAI-style function tool definition."""
    props = props or {}
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": {k: {"type": "string", "description": v}
                                                         for k, v in props.items()},
                       "required": list(props)}}}


TOOLS = [
    _tool("find_item", "Guide the user to this item with beeps and directions.",
          {"item": "watchlist item name"}),
    _tool("where_is_item", "Tell the user where this item was last seen.",
          {"item": "watchlist item name"}),
    _tool("stop_guidance", "Stop guiding the user."),
    _tool("set_room", "The user says which room they are in.", {"room": "room name"}),
]


def build_system_prompt(items: list[dict], rooms: list[str], current_room: str | None) -> str:
    """Short, plain system prompt with the stored watchlist and rooms injected."""
    watch = "; ".join(i["name"] + (f" (also: {', '.join(i['aliases'])})" if i["aliases"] else "")
                      for i in items) or "none"
    where = f" They are in the {current_room}." if current_room else ""
    return (
        "You are Sightline, a voice helper for a blind or low-vision person. They talk by voice "
        "and hear your reply read aloud. Keep replies under 15 words, in plain words.\n"
        "Decide what they want: find an item (call find_item), ask where an item was last seen "
        "(call where_is_item), stop guidance (call stop_guidance), say which room they are in "
        "(call set_room), or just talk (reply with text only).\n"
        f"Items you can find: {watch}. Rooms: {', '.join(rooms) or 'none'}.{where}\n"
        "Only use item names from that list. Never say where an item is: the app answers "
        "locations. If the item is unclear or not on the list, ask one short question."
    )


class LLMClient(ABC):
    """chat(messages, tools) -> LLMResult. messages are OpenAI-style {role, content} dicts."""
    provider = "unknown"

    @abstractmethod
    def chat(self, messages: list[dict], tools: list[dict]) -> LLMResult:
        """Raise LLMError on any failure."""


# ---- keyword (default, no network) -------------------------------------------------------

_NOISE = re.compile(r"\b(please|for me|right now|again)\b")
_STOP = re.compile(
    r"\b("
    r"stop(?:ped|ping| guiding| guidance)?|"
    r"cancel|"
    r"never ?mind|"
    r"that'?s enough|"
    r"all good|i'?m good|"
    r"done|"
    r"(?:i(?:'ve| have|'m| am)? )?(?:got|found|have|grabbed|reached|picked up|holding) "
    r"(?:it|them|that|em|(?:the|my|a|an) [a-z0-9 ]+)|"
    r"(?:i(?:'ve| have|'m| am)? )?(?:got|found|grabbed|picked up) [a-z0-9 ]+"
    r")\b"
)
_ROOM = re.compile(r"\b(?:i'?m|i am|we'?re|we are) (?:now )?in (?:the |my )?([a-z ]+)$")
_WHERE = re.compile(r"\b(?:where(?:'?s| is| are| did i (?:put|leave|last see|last have))"
                    r"|have you seen|when did i last see|show me where)\s+(.+)")
_FIND = re.compile(r"\b(?:can'?t find|cannot find|couldn'?t find|can not find|guide me to|take me to"
                   r"|lead me to|walk me to|bring me to|direct me to|point me to|how do i get to"
                   r"|help me find|look for|search for|locate|find)\s+(.+)")
_THANKS = re.compile(r"\b(thanks|thank you|cheers)\b")
HELP_TEXT = "Try: where's my wallet, or find my keys."


def _clean(text: str) -> str:
    """Lowercase, drop punctuation and filler, drop a leading my/the."""
    t = _NOISE.sub("", re.sub(r"[?.!,]", "", text.lower()))
    return re.sub(r"^(my|the|a|an)\s+", "", re.sub(r"\s+", " ", t).strip())


class KeywordLLM(LLMClient):
    """Regex intent matching. Used as the default and as the fallback for every other client."""

    def __init__(self, provider: str = "mock") -> None:
        self.provider = provider

    def chat(self, messages: list[dict], tools: list[dict]) -> LLMResult:
        user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        t = re.sub(r"\s+", " ", re.sub(r"[?.!,]", "", user.lower())).strip()
        if _STOP.search(t):
            return LLMResult(tool_calls=[ToolCall("stop_guidance", {})])
        for pattern, tool, arg in ((_ROOM, "set_room", "room"), (_WHERE, "where_is_item", "item"),
                                   (_FIND, "find_item", "item")):
            m = pattern.search(t)
            if m and _clean(m.group(1)):
                return LLMResult(tool_calls=[ToolCall(tool, {arg: _clean(m.group(1))})])
        if _THANKS.search(t):
            return LLMResult(text="You're welcome.")
        return LLMResult(text=HELP_TEXT)


# ---- OpenAI-style HTTP clients -----------------------------------------------------------

def parse_chat_response(data: dict) -> LLMResult:
    """Parse an OpenAI-style chat completion. Also accepts camelCase `toolCalls` (unverified)."""
    try:
        msg = data["choices"][0]["message"]
        calls = []
        for tc in msg.get("tool_calls") or msg.get("toolCalls") or []:
            fn = tc["function"]
            args = fn.get("arguments") or {}
            if isinstance(args, str):
                args = json.loads(args or "{}")
            calls.append(ToolCall(fn["name"], args if isinstance(args, dict) else {}))
        return LLMResult(text=msg.get("content") or "", tool_calls=calls)
    except (KeyError, IndexError, TypeError, AttributeError, ValueError) as e:
        raise LLMError(f"unexpected response shape ({type(e).__name__})") from None


class _HTTPChatLLM(LLMClient):
    """Shared OpenAI-compatible wire format. Subclasses supply settings and field mapping."""

    def __init__(self, transport: httpx.BaseTransport | None = None) -> None:
        self._transport = transport  # tests inject httpx.MockTransport; no real network

    def _settings(self) -> tuple[str, str, str]:
        """Return (base_url, api_key, model) or raise LLMError if not configured."""
        raise NotImplementedError

    def _fields(self, body: dict) -> dict:
        """Map OpenAI-style request field names to the server's names."""
        return body

    def _get(self, path: str) -> dict:
        base, key, _ = self._settings()
        return self._request("GET", f"{base}{path}", key)

    def _request(self, method: str, url: str, key: str, body: dict | None = None) -> dict:
        """One HTTP call; every failure becomes LLMError without leaking the token or body."""
        timeout = float(os.getenv("LLM_TIMEOUT_S") or DEFAULT_TIMEOUT_S)
        try:
            with httpx.Client(timeout=timeout, transport=self._transport, verify=_VERIFY) as c:
                r = c.request(method, url, headers={"Authorization": f"Bearer {key}"}, json=body)
            r.raise_for_status()
            return r.json()
        except httpx.TimeoutException:
            raise LLMError("timeout") from None
        except httpx.HTTPStatusError as e:
            raise LLMError(f"HTTP {e.response.status_code}") from None
        except (httpx.HTTPError, ValueError) as e:
            raise LLMError(type(e).__name__) from None

    def chat(self, messages: list[dict], tools: list[dict]) -> LLMResult:
        base, key, model = self._settings()
        body = self._fields({"model": model, "messages": messages, "tools": tools,
                             "temperature": 0.2})
        return parse_chat_response(self._request("POST", f"{base}/chat/completions", key, body))

    def list_models(self) -> list[str]:
        """Model ids from GET {base}/models (OpenAI-style response: {"data": [{"id": ...}]})."""
        try:
            return [m["id"] for m in self._get("/models")["data"]]
        except (KeyError, TypeError):
            raise LLMError("unexpected models response") from None


class PurpleLLM(_HTTPChatLLM):
    """UW Purple (nebulaONE) API.

    UNVERIFIED against Purple's OpenAPI spec (https://purple.uw.edu/docs/api/v1; the docs page
    renders client-side and could not be fetched). Assumed from the brief: POST
    {PURPLE_API_BASE}/chat/completions with Authorization: Bearer <token>. Unconfirmed:
      * exact request field casing (tool_choice vs toolChoice, max_tokens vs maxCompletionTokens)
      * the models path (assumed GET {base}/models) and response shape
      * the response shape of tool calls (parse_chat_response accepts tool_calls and toolCalls)
    PURPLE_MODEL is deliberately not defaulted: list ids with `python -m app.llm models`.
    """
    provider = "purple"
    # TODO: after checking the OpenAPI spec, fill this in, e.g. {"tool_choice": "toolChoice"}.
    # Only model/messages/tools/temperature are sent today, so nothing needs renaming yet.
    FIELD_NAMES: dict[str, str] = {}

    def _settings(self) -> tuple[str, str, str]:
        base = (os.getenv("PURPLE_API_BASE") or DEFAULT_PURPLE_BASE).rstrip("/")  # blank = default
        token, model = os.getenv("PURPLE_API_TOKEN", ""), os.getenv("PURPLE_MODEL", "")
        if not token:
            raise LLMNotConfigured("PURPLE_API_TOKEN not set")
        if not model:
            raise LLMNotConfigured("PURPLE_MODEL not set")
        return base, token, model

    def _fields(self, body: dict) -> dict:
        return {self.FIELD_NAMES.get(k, k): v for k, v in body.items()}


class OpenAICompatLLM(_HTTPChatLLM):
    """Any OpenAI-compatible server: LLM_BASE_URL, LLM_API_KEY, LLM_MODEL. Stand-in for Purple."""
    provider = "openai_compat"

    def _settings(self) -> tuple[str, str, str]:
        base, key, model = (os.getenv("LLM_BASE_URL", "").rstrip("/"), os.getenv("LLM_API_KEY", ""),
                            os.getenv("LLM_MODEL", ""))
        if not (base and key and model):
            raise LLMNotConfigured("LLM_BASE_URL, LLM_API_KEY and LLM_MODEL must all be set")
        return base, key, model


class GeminiLLM(OpenAICompatLLM):
    """Google Gemini through its OpenAI-compatible endpoint (same tool-calling code path).

    Per Google's docs: POST {base}/chat/completions with Authorization: Bearer <GEMINI_API_KEY>,
    tools as {"type": "function", ...}. GEMINI_MODEL has no default because model names change
    (see .env.example). UNVERIFIED: Google's page does not show the tool_calls response shape or
    say whether `arguments` is a JSON string; parse_chat_response handles string and dict.
    """
    provider = "gemini"

    def _settings(self) -> tuple[str, str, str]:
        base = (os.getenv("GEMINI_BASE_URL") or DEFAULT_GEMINI_BASE).rstrip("/")  # blank = default
        key, model = os.getenv("GEMINI_API_KEY", ""), os.getenv("GEMINI_MODEL", "")
        if not key:
            raise LLMNotConfigured("GEMINI_API_KEY not set")
        if not model:
            raise LLMNotConfigured("GEMINI_MODEL not set")
        return base, key, model


# ---- selection + fallback ----------------------------------------------------------------

def _make(name: str) -> LLMClient:
    """Build the client for a provider name."""
    return {"purple": PurpleLLM, "gemini": GeminiLLM, "openai_compat": OpenAICompatLLM}[name]()


def get_llm() -> LLMClient:
    """Client chosen by LLM_PROVIDER (default mock)."""
    provider = os.getenv("LLM_PROVIDER", "mock").lower()
    if provider in ("purple", "gemini", "openai_compat"):
        return _make(provider)
    if provider != "mock":
        log.warning("Unknown LLM_PROVIDER %r; using keyword matching", provider)
    return KeywordLLM("mock")


def get_chain() -> list[LLMClient]:
    """The selected provider followed by the later ones in PROVIDER_ORDER (purple -> gemini)."""
    first = get_llm()
    later = []
    if first.provider in PROVIDER_ORDER:
        later = [_make(n) for n in PROVIDER_ORDER[PROVIDER_ORDER.index(first.provider) + 1:]]
    return [first, *later]


def chat_with_fallback(messages: list[dict], tools: list[dict] | None = None) -> tuple[LLMResult, str]:
    """Try each provider in the chain, then KeywordLLM. Returns (result, provider that answered)."""
    tools = TOOLS if tools is None else tools
    for client in get_chain():
        try:
            return client.chat(messages, tools), client.provider
        except LLMNotConfigured as e:
            log.info("LLM %s skipped (%s)", client.provider, e)
        except Exception as e:  # noqa: BLE001 - the demo must never break on an LLM problem
            log.warning("LLM %s failed (%s); trying next", client.provider,
                        e if isinstance(e, LLMError) else type(e).__name__)
    return KeywordLLM("keyword_fallback").chat(messages, tools), "keyword_fallback"


def _main() -> None:
    """`python -m app.llm models`: print model ids the configured provider offers."""
    import sys
    from pathlib import Path

    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    client = get_llm()
    if len(sys.argv) < 2 or sys.argv[1] != "models" or not isinstance(client, _HTTPChatLLM):
        sys.exit("usage: python -m app.llm models   (needs LLM_PROVIDER=purple, gemini or openai_compat)")
    try:
        print("\n".join(client.list_models()))
    except LLMError as e:
        sys.exit(f"could not list models: {e}")


if __name__ == "__main__":
    _main()
