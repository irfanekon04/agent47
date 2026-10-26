"""Presentation layer for the agent loop.

``run_turn`` emits coarse events instead of printing directly. The plain
renderer reproduces the original line-based output, while the TUI renderer in
``tui.py`` forwards the same events to Textual widgets.
"""

import json

HELP_TEXT = """Commands:
  /help          Show this help
  /new           Save this session and start a new one
  /sessions      List saved sessions
  /history       Show this session's turn, message, token, and compaction counts
  /compact       Summarize older turns now instead of waiting for the limit
  /exit, /quit   Save and exit

Anything else is sent to the agent."""

KEYS_TEXT = "Enter send  ·  Ctrl+J newline  ·  Esc cancel  ·  Ctrl+Q quit"


def format_arguments(arguments: str | dict | None, limit: int = 90) -> str:
    """Render tool arguments as a short, single-line summary."""
    if not arguments:
        return ""

    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
        except json.JSONDecodeError:
            summary = arguments
        else:
            summary = _format_mapping(parsed)
    elif isinstance(arguments, dict):
        summary = _format_mapping(arguments)
    else:
        summary = str(arguments)

    summary = " ".join(summary.split())
    if len(summary) > limit:
        summary = summary[: limit - 1] + "…"
    return summary


def _format_mapping(value) -> str:
    if not isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)

    parts = []
    for key, item in value.items():
        if key == "working_directory":
            continue
        if isinstance(item, str):
            parts.append(f"{key}={item}")
        else:
            parts.append(f"{key}={json.dumps(item, ensure_ascii=False)}")
    return " ".join(parts)


class Renderer:
    """No-op base class describing the events the agent loop emits."""

    verbose = False
    live = False

    def assistant_delta(self, text: str) -> None:
        pass

    def assistant_end(self, text: str, tool_calls: bool = False) -> None:
        pass

    def tool_call(self, call_id: str, name: str, arguments: str) -> None:
        pass

    def tool_result(self, call_id: str, name: str, content: str) -> None:
        pass

    def tokens(
        self,
        prompt: int | None,
        completion: int | None,
        limit: int,
        estimated: bool = False,
    ) -> None:
        pass

    def compaction(self, before: int, after: int, summarized: int, kept: int) -> None:
        pass

    def notice(self, text: str) -> None:
        pass

    def error(self, text: str) -> None:
        pass


class PlainRenderer(Renderer):
    """Line-based output, used for one-shot runs and ``--plain``."""

    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        self._started = False

    def assistant_delta(self, text: str) -> None:
        if not text:
            return
        if not self._started:
            print()
            self._started = True
        print(text, end="", flush=True)

    def assistant_end(self, text: str, tool_calls: bool = False) -> None:
        if text:
            if self._started:
                print()
            else:
                print(f"\n{text}\n")
        elif not tool_calls:
            print("\n(no response)\n")

        self._started = False

    def tool_call(self, call_id: str, name: str, arguments: str) -> None:
        if self.verbose:
            print(f" - Calling function: {name}({format_arguments(arguments)})")
        else:
            print(f" - Calling function: {name}")

    def tool_result(self, call_id: str, name: str, content: str) -> None:
        if self.verbose:
            print(f"-> {content}")

    def tokens(
        self,
        prompt: int | None,
        completion: int | None,
        limit: int,
        estimated: bool = False,
    ) -> None:
        if not self.verbose:
            return

        if prompt is None:
            print("[tokens] provider did not return usage")
            return

        prefix = "~" if estimated else ""
        completion_display = "?" if completion is None else f"{prefix}{completion}"
        print(
            f"[tokens] prompt={prefix}{prompt} completion={completion_display} "
            f"(limit {limit})"
        )

    def compaction(self, before: int, after: int, summarized: int, kept: int) -> None:
        print(
            f"[compacted {before} -> {after} est. tokens: "
            f"summarized {summarized} messages, kept {kept}]"
        )

    def notice(self, text: str) -> None:
        print(text)

    def error(self, text: str) -> None:
        print(f"Error: {text}")
