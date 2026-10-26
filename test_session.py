import tempfile
from pathlib import Path
from types import SimpleNamespace

import session as session_module
from session import (
    Session,
    compact_session,
    estimate_tokens,
    find_cut_index,
    load_session,
    maybe_compact,
    needs_compaction,
    sanitize_messages,
    truncate_tool_result,
)


class FakeSummarizer:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.chat = SimpleNamespace(completions=self)

    def create(self, **kwargs):
        if self.fail:
            raise RuntimeError("summarizer down")
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content="- user wanted a calculator fix\n- changed calc.py"
                    )
                )
            ],
            usage=SimpleNamespace(prompt_tokens=100, completion_tokens=20),
        )


def build_messages(turns: int = 5) -> list[dict]:
    messages = []

    for turn in range(turns):
        call_id = f"call_{turn}"
        messages.append({"role": "user", "content": f"request {turn}"})
        messages.append(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {
                            "name": "get_file_content",
                            "arguments": '{"path":"big.py"}',
                        },
                    }
                ],
            }
        )
        messages.append({"role": "tool", "tool_call_id": call_id, "content": "x" * 20_000})
        messages.append({"role": "assistant", "content": f"finished turn {turn}"})

    return messages


def assert_pairs_intact(messages: list[dict]) -> None:
    pending = set()

    for message in messages:
        if message.get("role") == "assistant":
            for call in message.get("tool_calls") or []:
                pending.add(call["id"])
        elif message.get("role") == "tool":
            assert message["tool_call_id"] in pending, message
            pending.remove(message["tool_call_id"])

    assert not pending, f"dangling tool calls: {pending}"


def main() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        session_module.SESSION_DIR = Path(temp_dir)

        # Compaction summarizes older turns, keeps the newest one, and injects the summary.
        session = Session("compact-test", messages=build_messages())
        session.last_prompt_tokens = 20_000
        assert needs_compaction(session, 16_000)

        assert maybe_compact(FakeSummarizer(), session, 16_000) is True
        assert session.compactions == 1
        assert session.summary.startswith("- user wanted")
        assert session.messages[1]["role"] == "user", "cut must land on a user message"
        assert session.messages[1]["content"] == "request 4", "newest turn should be kept"
        assert "Summary of earlier conversation" in session.messages[0]["content"]
        assert_pairs_intact(session.messages)
        assert session.prompt_tokens == 100 and session.completion_tokens == 20
        assert estimate_tokens(session.messages) < 16_000

        # Save/load keeps the summary and rebuilds the system prompt.
        session.save()
        loaded = load_session("compact-test")
        assert loaded.summary == session.summary
        assert loaded.compactions == 1
        assert "Summary of earlier conversation" in loaded.messages[0]["content"]
        assert loaded.messages[1:] == session.messages[1:]

        # A failed summarizer falls back to a deterministic summary.
        fallback = Session("compact-fallback", messages=build_messages())
        fallback.last_prompt_tokens = 20_000
        assert maybe_compact(FakeSummarizer(fail=True), fallback, 16_000) is True
        assert "Automatic summary" in fallback.summary
        assert_pairs_intact(fallback.messages)

        # Nothing to cut with a single turn.
        single = Session("single", messages=build_messages(turns=1))
        assert find_cut_index(single.messages) is None
        assert not needs_compaction(single, 16_000)

        # Tool outputs over the cap are truncated.
        truncated = truncate_tool_result("a" * 20_000)
        assert len(truncated) < 20_000
        assert "truncated 8000 characters" in truncated
        assert truncate_tool_result("short") == "short"

        # Small contexts are left alone.
        small = Session("small", messages=build_messages(turns=1))
        small.messages[2]["content"] = "small"
        assert not maybe_compact(FakeSummarizer(), small, 16_000)

        assert compact_session(FakeSummarizer(), small, 16_000) is False

        # Old or non-conforming histories are repaired on load: null content
        # becomes a string and empty assistant turns are dropped.
        repaired = sanitize_messages(
            [
                {"role": "system", "content": "system"},
                {"role": "assistant", "content": None},
                {"role": "user", "content": None},
                {"role": "assistant", "content": None, "tool_calls": [{"id": "c1"}]},
                {"role": "tool", "tool_call_id": "c1", "content": None},
            ]
        )
        assert repaired[0]["content"] == "system"
        assert repaired[1] == {"role": "user", "content": ""}
        assert repaired[2]["role"] == "assistant" and repaired[2]["tool_calls"]
        assert repaired[3]["content"] == ""

    print("All session/compaction checks passed.")


if __name__ == "__main__":
    main()
