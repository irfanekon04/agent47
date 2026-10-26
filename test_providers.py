from types import SimpleNamespace

import providers
from providers import complete, normalize_messages


def chunk(content=None, tool_calls=None, usage=None, has_choice=True):
    delta = SimpleNamespace(content=content, tool_calls=tool_calls)
    choices = [SimpleNamespace(delta=delta)] if has_choice else []
    return SimpleNamespace(choices=choices, usage=usage)


def tool_delta(index, call_id=None, name=None, arguments=None):
    return SimpleNamespace(
        index=index,
        id=call_id,
        function=SimpleNamespace(name=name, arguments=arguments),
    )


class FakeClient:
    def __init__(self, stream_chunks=None, block_response=None, fail_stream=False):
        self.stream_chunks = stream_chunks or []
        self.block_response = block_response
        self.fail_stream = fail_stream
        self.calls = []
        self.chat = SimpleNamespace(completions=self)

    def create(self, **kwargs):
        self.calls.append(kwargs)

        if kwargs.get("stream"):
            if self.fail_stream:
                raise ValueError("streaming not supported")
            return iter(self.stream_chunks)

        return self.block_response


def main() -> None:
    # Streaming text is forwarded and assembled into one message.
    deltas = []
    client = FakeClient(
        stream_chunks=[
            chunk("Hello"),
            chunk(" world"),
            chunk(usage=SimpleNamespace(prompt_tokens=12, completion_tokens=2), has_choice=False),
        ]
    )
    result = complete(client, [], [], on_delta=deltas.append)

    assert deltas == ["Hello", " world"], deltas
    assert result.message == {"role": "assistant", "content": "Hello world"}
    assert result.prompt_tokens == 12 and result.completion_tokens == 2

    # Tool calls are accumulated across chunks, including split arguments.
    client = FakeClient(
        stream_chunks=[
            chunk(tool_calls=[tool_delta(0, call_id="call_1", name="get_file", arguments='{"pa')]),
            chunk(tool_calls=[tool_delta(0, arguments='th": "calc.py"}')]),
        ]
    )
    result = complete(client, [], [], on_delta=lambda text: None)

    assert "tool_calls" in result.message
    call = result.message["tool_calls"][0]
    assert call["id"] == "call_1"
    assert call["function"]["name"] == "get_file"
    assert call["function"]["arguments"] == '{"path": "calc.py"}'
    assert result.prompt_tokens is None

    # Missing tool-call ids get a synthetic one so the history stays valid.
    client = FakeClient(
        stream_chunks=[chunk(tool_calls=[tool_delta(0, name="get_files_info")])]
    )
    result = complete(client, [], [])
    assert result.message["tool_calls"][0]["id"].startswith("call_")
    assert result.message["tool_calls"][0]["function"]["arguments"] == "{}"

    # A provider that rejects streaming is retried without it.
    block_response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="blocking works", tool_calls=None, refusal=None)
            )
        ],
        usage=SimpleNamespace(prompt_tokens=7, completion_tokens=3),
    )
    client = FakeClient(fail_stream=True, block_response=block_response)
    deltas = []
    result = complete(client, [], [], on_delta=deltas.append)

    assert result.message["content"] == "blocking works"
    assert deltas == []
    assert [call.get("stream", False) for call in client.calls] == [True, False]
    assert result.prompt_tokens == 7

    # Strict local servers reject null content, so it is normalized away
    # without mutating the caller's history.
    original = [
        {"role": "assistant", "content": None},
        {"role": "tool", "tool_call_id": "c1", "content": None},
        {"role": "user", "content": "hi"},
    ]
    normalized = normalize_messages(original)
    assert normalized[0]["content"] == ""
    assert normalized[1]["content"] == ""
    assert original[0]["content"] is None

    client = FakeClient(stream_chunks=[chunk("ok")])
    complete(client, original, [])
    sent = client.calls[0]["messages"]
    assert sent[0]["content"] == "" and sent[1]["content"] == ""
    assert original[0]["content"] is None

    print("All provider checks passed.")


if __name__ == "__main__":
    main()
