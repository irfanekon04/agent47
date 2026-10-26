import asyncio
import tempfile
from pathlib import Path
from types import SimpleNamespace

import session as session_module
from session import Session
from tui import Agent47App


def text_chunk(content):
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content=content, tool_calls=None))],
        usage=None,
    )


def tool_chunk(index=0, call_id=None, name=None, arguments=None):
    tool_call = SimpleNamespace(
        index=index,
        id=call_id,
        function=SimpleNamespace(name=name, arguments=arguments),
    )
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content=None, tool_calls=[tool_call]))],
        usage=None,
    )


class FakeCompletions:
    def __init__(self):
        self.calls = 0

    def create(self, **kwargs):
        assert kwargs.get("stream"), "the TUI should stream"
        self.calls += 1

        if self.calls == 1:
            return iter(
                [
                    tool_chunk(
                        call_id="call_1",
                        name="get_files_info",
                        arguments='{"directory": "."}',
                    ),
                    tool_chunk(arguments=None),
                ]
            )

        return iter([text_chunk("Done.")])


class FakeClient:
    def __init__(self):
        self.chat = SimpleNamespace(completions=FakeCompletions())


async def main():
    with tempfile.TemporaryDirectory() as temp_dir:
        session_module.SESSION_DIR = Path(temp_dir)

        app = Agent47App(
            client=FakeClient(),
            session=Session("tui-smoke"),
            context_limit=16_000,
        )

        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            app.prompt.focus()

            await pilot.press("a", "ctrl+j", "b")
            assert app.prompt.text == "a\nb", app.prompt.text
            app.prompt.clear()

            await pilot.press(*"list the calculator files")
            await pilot.press("enter")

            for _ in range(400):
                await pilot.pause(0.01)
                if not app._busy:
                    break

            assert not app._busy, "turn did not finish"
            assert app.session.messages[-1]["content"] == "Done."
            assert app.session.last_prompt_tokens > 0
            assert app.session.messages[2]["tool_calls"][0]["id"] == "call_1"
            assert len(app.query("Collapsible")) == 1, "tool call should be rendered"

            await app._handle_command("/help")
            await pilot.pause()

    print("All TUI checks passed.")


if __name__ == "__main__":
    asyncio.run(main())
