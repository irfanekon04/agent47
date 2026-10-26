"""Chat-completion calls with optional streaming.

Streaming is used so the TUI can render tokens as they arrive. Some
OpenAI-compatible servers do not support it, so a failed stream falls back to
a blocking request as long as nothing was emitted yet.
"""

from dataclasses import dataclass
from uuid import uuid4

from openai import OpenAI

from config import MODEL, STREAM_RESPONSES


@dataclass
class Completion:
    message: dict
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


def assistant_message_to_dict(message) -> dict:
    """Convert the SDK message object into a plain, JSON-serializable dict."""
    data = {"role": "assistant", "content": message.content or ""}

    if message.tool_calls:
        data["tool_calls"] = [
            tool_call.model_dump(exclude_none=True) for tool_call in message.tool_calls
        ]

    if message.refusal:
        data["refusal"] = message.refusal

    return data


def normalize_messages(messages: list[dict]) -> list[dict]:
    """Make history safe for strict OpenAI-compatible servers.

    Several local servers (notably Ollama) reject ``content: null`` with
    "invalid message content type: <nil>", so empty content is always sent as
    an empty string.
    """
    normalized = []

    for message in messages:
        item = dict(message)
        if item.get("content") is None:
            item["content"] = ""
        normalized.append(item)

    return normalized


def complete(
    client: OpenAI,
    messages: list[dict],
    tools: list[dict],
    on_delta=None,
) -> Completion:
    """Run one completion, streaming when possible."""
    messages = normalize_messages(messages)

    if not STREAM_RESPONSES:
        return _blocking(client, messages, tools)

    emitted = False

    def handle_delta(text: str) -> None:
        nonlocal emitted
        if not text:
            return
        emitted = True
        if on_delta is not None:
            on_delta(text)

    try:
        return _streaming(client, messages, tools, handle_delta)
    except Exception:
        # A server may reject stream=True before sending anything. Retrying
        # is only safe while no text has reached the user.
        if emitted:
            raise
        return _blocking(client, messages, tools)


def _blocking(client: OpenAI, messages: list[dict], tools: list[dict]) -> Completion:
    response = client.chat.completions.create(
        model=MODEL,
        messages=messages,
        tools=tools,
    )

    usage = response.usage
    return Completion(
        message=assistant_message_to_dict(response.choices[0].message),
        prompt_tokens=usage.prompt_tokens if usage else None,
        completion_tokens=usage.completion_tokens if usage else None,
    )


def _streaming(client: OpenAI, messages: list[dict], tools: list[dict], on_delta) -> Completion:
    stream = client.chat.completions.create(
        model=MODEL,
        messages=messages,
        tools=tools,
        stream=True,
    )

    text_parts: list[str] = []
    tool_calls: dict[int, dict] = {}
    prompt_tokens: int | None = None
    completion_tokens: int | None = None

    for chunk in stream:
        usage = getattr(chunk, "usage", None)
        if usage is not None:
            prompt_tokens = usage.prompt_tokens
            completion_tokens = usage.completion_tokens

        if not chunk.choices:
            continue

        delta = chunk.choices[0].delta

        if getattr(delta, "content", None):
            text_parts.append(delta.content)
            on_delta(delta.content)

        for tool_delta in getattr(delta, "tool_calls", None) or []:
            entry = tool_calls.setdefault(
                tool_delta.index,
                {
                    "id": "",
                    "type": "function",
                    "function": {"name": "", "arguments": ""},
                },
            )

            if tool_delta.id:
                entry["id"] = tool_delta.id

            function = getattr(tool_delta, "function", None)
            if function is None:
                continue
            if function.name:
                entry["function"]["name"] += function.name
            if function.arguments:
                entry["function"]["arguments"] += function.arguments

    message: dict = {"role": "assistant", "content": "".join(text_parts)}

    if tool_calls:
        ordered = []
        for index in sorted(tool_calls):
            entry = tool_calls[index]
            if not entry["id"]:
                entry["id"] = f"call_{uuid4().hex[:12]}"
            if not entry["function"]["arguments"]:
                entry["function"]["arguments"] = "{}"
            ordered.append(entry)
        message["tool_calls"] = ordered

    return Completion(
        message=message,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )
