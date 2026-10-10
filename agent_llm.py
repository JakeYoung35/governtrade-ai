"""Tool-calling conversations with the agent's configured model provider (OpenAI, Anthropic or Google Gemini).

One small class per provider keeps the provider's own message format, so a model's turns (including Gemini's
thought signatures) are sent back exactly as received. Errors never include the API key or the provider's
response body. Standard library only, like providers.py.
"""
import json
from dataclasses import dataclass, field
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

TIMEOUT_SECONDS = 60  # per model call; agent_chat.py also bounds the whole turn
# Output caps per model call. OpenAI and Gemini count reasoning ("thinking") tokens toward their cap, so it is
# higher there; agent_chat.py still truncates the saved reply to fit the database.
MAX_OUTPUT_TOKENS = 2048
MAX_OUTPUT_TOKENS_WITH_REASONING = 8192


class ModelError(Exception):
    """A provider call failed. The message is safe to show the user."""


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass(frozen=True)
class ModelTurn:
    text: str
    tool_calls: tuple = field(default_factory=tuple)


def post_json(url, headers, body, timeout=TIMEOUT_SECONDS):
    """POST JSON and return the decoded response. Raises ModelError with a user-safe message."""
    request = Request(url, data=json.dumps(body).encode(), method="POST",
                      headers={"Content-Type": "application/json", **headers})
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except HTTPError as e:
        reason = {400: "rejected the request", 401: "rejected the API key", 403: "denied access to this model",
                  404: "does not offer this model", 429: "is rate limiting or out of credit"}.get(e.code, f"returned HTTP {e.code}")
        if e.code in (400, 429):
            # Classify billing failures without echoing arbitrary provider text into saved chats.
            try:
                payload = json.loads(e.read(8192))
                error = payload.get("error", {}) if isinstance(payload, dict) else {}
                message = str(error.get("message", "")).lower() if isinstance(error, dict) else ""
                code = error.get("code") if isinstance(error, dict) else None
                if code == "insufficient_quota" or any(phrase in message for phrase in (
                        "credit balance is too low", "insufficient credit", "out of credit", "exhausted your credits")):
                    reason = "is out of API credit or has reached its spending limit; check your provider billing settings"
                elif "billing" in message:
                    reason = "reported a billing issue; check your provider billing settings"
            except (OSError, ValueError, TypeError):
                pass  # Empty, malformed or unreadable error bodies retain the HTTP-status fallback.
        raise ModelError(f"The model provider {reason}.") from None
    except (URLError, TimeoutError, OSError, ValueError):
        raise ModelError("The model provider could not be reached or sent an unreadable response.") from None


def _arguments(raw):
    """Tool arguments as a dict. Malformed JSON becomes an empty dict, which the tool reports as missing fields."""
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw or "{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _alternating(history):
    """(role, text) pairs with 'user'/'assistant' roles, merged so roles alternate and the first is 'user'."""
    merged = []
    for role, text in history:
        if merged and merged[-1][0] == role:
            merged[-1] = (role, merged[-1][1] + "\n\n" + text)
        else:
            merged.append((role, text))
    while merged and merged[0][0] != "user":
        merged.pop(0)
    return merged


class AnthropicConversation:
    URL = "https://api.anthropic.com/v1/messages"

    def __init__(self, model, key, system, tools, history, post=post_json):
        self.model, self.key, self.system, self.post = model, key, system, post
        self.tools = [{"name": t["name"], "description": t["description"], "input_schema": t["parameters"]} for t in tools]
        self.messages = [{"role": role, "content": text} for role, text in _alternating(history)]

    def step(self):
        # The system prompt and tools are identical on every step of a turn, so cache them (tools come first in the
        # cache order, so a breakpoint on the system block covers both).
        body = {"model": self.model, "max_tokens": MAX_OUTPUT_TOKENS, "messages": self.messages,
                "system": [{"type": "text", "text": self.system, "cache_control": {"type": "ephemeral"}}]}
        if self.tools:
            body["tools"] = self.tools
        data = self.post(self.URL, {"x-api-key": self.key, "anthropic-version": "2023-06-01"}, body)
        content = data.get("content") if isinstance(data, dict) else None
        if not isinstance(content, list):
            raise ModelError("The model provider sent an unreadable response.")
        try:
            text = "".join(b.get("text", "") for b in content if b.get("type") == "text")
            calls = tuple(ToolCall(b["id"], b["name"], _arguments(b.get("input"))) for b in content if b.get("type") == "tool_use")
        except (KeyError, TypeError, AttributeError):
            raise ModelError("The model provider sent an unreadable response.") from None
        self.messages.append({"role": "assistant", "content": content})
        return ModelTurn(text.strip(), calls)

    def add_tool_results(self, results):
        self.messages.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": call.id, "content": json.dumps(result)} for call, result in results]})


class OpenAIConversation:
    URL = "https://api.openai.com/v1/chat/completions"

    def __init__(self, model, key, system, tools, history, post=post_json):
        self.model, self.key, self.post = model, key, post
        self.tools = [{"type": "function", "function": t} for t in tools]
        self.messages = [{"role": "system", "content": system}] + [
            {"role": role, "content": text} for role, text in _alternating(history)]

    def step(self):
        body = {"model": self.model, "messages": self.messages, "max_completion_tokens": MAX_OUTPUT_TOKENS_WITH_REASONING}
        if self.tools:
            body["tools"] = self.tools
        data = self.post(self.URL, {"Authorization": "Bearer " + self.key}, body)
        try:
            message = data["choices"][0]["message"]
            # Only function calls can be answered; keeping any other kind in the history would leave a call without
            # a tool response, which OpenAI rejects on the next request.
            raw_calls = [c for c in message.get("tool_calls") or [] if c.get("type", "function") == "function"]
            calls = tuple(ToolCall(c["id"], c["function"]["name"], _arguments(c["function"].get("arguments")))
                          for c in raw_calls)
        except (KeyError, IndexError, TypeError, AttributeError):
            raise ModelError("The model provider sent an unreadable response.") from None
        self.messages.append({"role": "assistant", "content": message.get("content"),
                              **({"tool_calls": raw_calls} if raw_calls else {})})
        return ModelTurn((message.get("content") or "").strip(), calls)

    def add_tool_results(self, results):
        for call, result in results:
            self.messages.append({"role": "tool", "tool_call_id": call.id, "content": json.dumps(result)})


def _gemini_schema(schema):
    """JSON Schema to Gemini's OpenAPI-style schema: upper-case types, and only the keywords it accepts."""
    out = {}
    for key, value in schema.items():
        if key == "type":
            out["type"] = value.upper()
        elif key == "properties":
            out["properties"] = {name: _gemini_schema(sub) for name, sub in value.items()}
        elif key == "items":
            out["items"] = _gemini_schema(value)
        elif key in ("description", "enum", "required", "minimum", "maximum", "format", "nullable"):
            out[key] = value
    return out


class GeminiConversation:
    URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    def __init__(self, model, key, system, tools, history, post=post_json):
        self.model, self.key, self.system, self.post = model, key, system, post
        self.tools = [{"functionDeclarations": [
            {"name": t["name"], "description": t["description"], "parameters": _gemini_schema(t["parameters"])}
            for t in tools]}] if tools else []
        self.contents = [{"role": "user" if role == "user" else "model", "parts": [{"text": text}]}
                         for role, text in _alternating(history)]
        self.calls = 0

    def step(self):
        body = {"systemInstruction": {"parts": [{"text": self.system}]}, "contents": self.contents,
                "generationConfig": {"maxOutputTokens": MAX_OUTPUT_TOKENS_WITH_REASONING}}
        if self.tools:
            body["tools"] = self.tools
        data = self.post(self.URL.format(model=self.model), {"x-goog-api-key": self.key}, body)
        try:
            content = data["candidates"][0]["content"]
        except (KeyError, IndexError, TypeError):
            raise ModelError("The model provider returned no answer (it may have been blocked by a safety filter).") from None
        content = {"role": "model", "parts": content.get("parts") or []}
        self.contents.append(content)  # sent back unchanged, including any thought signatures
        text = "".join(p.get("text", "") for p in content["parts"] if not p.get("thought"))
        calls = []
        try:
            for part in content["parts"]:
                if "functionCall" in part:
                    self.calls += 1  # Gemini calls carry no id; responses are matched by name and order
                    calls.append(ToolCall(f"gemini-{self.calls}", part["functionCall"]["name"],
                                          _arguments(part["functionCall"].get("args"))))
        except (KeyError, TypeError, AttributeError):
            raise ModelError("The model provider sent an unreadable response.") from None
        return ModelTurn(text.strip(), tuple(calls))

    def add_tool_results(self, results):
        self.contents.append({"role": "user", "parts": [
            {"functionResponse": {"name": call.name, "response": result}} for call, result in results]})


CONVERSATIONS = {"anthropic": AnthropicConversation, "openai": OpenAIConversation, "google": GeminiConversation}


def connect(provider, model, key, system, tools, history, post=post_json):
    """A conversation with the provider. tools: [{name, description, parameters (JSON Schema)}];
    history: [(role 'user'|'assistant', text)] ending with the user's new message."""
    if provider not in CONVERSATIONS:
        raise ModelError("This agent's model provider is not supported.")
    return CONVERSATIONS[provider](model, key, system, tools, history, post)
