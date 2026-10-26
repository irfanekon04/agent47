from types import SimpleNamespace

from main import run_turn
from renderer import PlainRenderer
from session import Session


def stream_chunk(content):
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content=content, tool_calls=None))],
        usage=None,
    )


class FakeCompletions:
    def __init__(self, responses):
        self.responses = list(responses)

    def create(self, **kwargs):
        return iter(self.responses.pop(0))


class FakeClient:
    def __init__(self, responses):
        self.chat = SimpleNamespace(completions=FakeCompletions(responses))


class RecordingRenderer(PlainRenderer):
    def __init__(self):
        super().__init__()
        self.notices = []

    def notice(self, text: str) -> None:
        self.notices.append(text)


def main() -> None:
    # An empty assistant turn is retried and never stored.
    renderer = RecordingRenderer()
    session = Session("empty-retry")
    client = FakeClient(
        [
            [stream_chunk(None)],
            [stream_chunk("Recovered answer.")],
        ]
    )

    assert run_turn(client, session, "hello", renderer, 16_000) is True
    assert [m["role"] for m in session.messages] == ["system", "user", "assistant"]
    assert session.messages[-1]["content"] == "Recovered answer."
    assert any("retrying once" in notice for notice in renderer.notices)

    # A second empty response ends the turn with a clear notice and still
    # leaves the history valid (no null/empty assistant message).
    renderer = RecordingRenderer()
    session = Session("empty-twice")
    client = FakeClient([[stream_chunk(None)], [stream_chunk(None)]])

    assert run_turn(client, session, "hello", renderer, 16_000) is True
    assert [m["role"] for m in session.messages] == ["system", "user"]
    assert any("another empty response" in notice for notice in renderer.notices)

    print("All agent checks passed.")


if __name__ == "__main__":
    main()
