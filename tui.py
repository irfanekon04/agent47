"""Full-screen Textual interface for Agent47."""

from __future__ import annotations

import threading
import time

from rich.markup import escape
from rich.text import Text
from textual import events, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Collapsible, Markdown, Static, TextArea

from config import MODEL
from errors import TurnCancelled
from main import run_turn
from renderer import HELP_TEXT, KEYS_TEXT, Renderer, format_arguments
from session import Session, collect_sessions, compact_session, new_session_id

SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
TOOL_RESULT_PREVIEW = 6_000


CSS = """
Screen {
    background: #0f1115;
    color: #d6d9e0;
}

#topbar {
    height: 1;
    background: #161a22;
    color: #8b93a7;
    padding: 0 1;
}

#conversation {
    height: 1fr;
    background: #0f1115;
    padding: 1 2 0 2;
    scrollbar-size-vertical: 1;
}

#conversation Static.user {
    margin: 1 0 0 0;
    padding: 0 1;
    background: #151a24;
    border-left: thick #7aa2f7;
}

#conversation Markdown {
    margin: 1 0 0 0;
    background: transparent;
}

#conversation .tool {
    margin: 0 0 0 1;
    padding: 0;
    background: transparent;
    border: none;
}

#conversation .tool CollapsibleTitle {
    background: transparent;
    color: #6b7385;
    padding: 0 1;
}

#conversation .note {
    margin: 0 0 0 1;
    color: #7c8499;
}

#conversation .note-error {
    margin: 0 0 0 1;
    color: #f7768e;
}

#status {
    height: 1;
    background: #161a22;
    color: #7c8499;
    padding: 0 1;
}

#composer {
    height: auto;
    max-height: 8;
    background: #12151c;
    border-top: solid #2a3040;
    padding: 0 1;
}

#prompt-symbol {
    width: 2;
    color: #7aa2f7;
    text-style: bold;
    padding: 0;
}

PromptArea {
    height: auto;
    min-height: 1;
    max-height: 6;
    background: #12151c;
    border: none;
    color: #e6e9f0;
}
"""


def fmt_tokens(value: int) -> str:
    if value >= 1000:
        return f"{value / 1000:.1f}k"
    return str(value)


def preview(content: str) -> str:
    if len(content) <= TOOL_RESULT_PREVIEW:
        return content
    omitted = len(content) - TOOL_RESULT_PREVIEW
    return f"{content[:TOOL_RESULT_PREVIEW]}\n… [{omitted} more characters]"


class PromptArea(TextArea):
    """Multi-line input where Enter sends and Ctrl+J inserts a newline."""

    async def _on_key(self, event: events.Key) -> None:
        if event.key == "enter":
            event.prevent_default()
            event.stop()
            await self.app.action_submit()
            return

        if event.key in ("shift+enter", "ctrl+j"):
            event.prevent_default()
            event.stop()
            self.insert("\n")
            return

        await super()._on_key(event)


class Agent47App(App):
    CSS = CSS
    TITLE = "Agent47"
    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit"),
        Binding("escape", "cancel_turn", "Cancel", show=False),
        Binding("ctrl+l", "clear_view", "Clear view", show=False),
    ]

    def __init__(
        self,
        client,
        session: Session,
        context_limit: int,
        verbose: bool = False,
    ) -> None:
        super().__init__()
        self.client = client
        self.session = session
        self.context_limit = context_limit
        self.verbose = verbose
        self.renderer = TuiRenderer(self)

        self._busy = False
        self._cancel = threading.Event()
        self._spinner_index = 0

        self._assistant_md: Markdown | None = None
        self._assistant_text = ""
        self._render_timer = None

        self._tool_widgets: dict[str, tuple[Collapsible, Static, str]] = {}

    # -- layout ---------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Static(id="topbar")
        yield VerticalScroll(id="conversation")
        yield Static(id="status")
        with Horizontal(id="composer"):
            yield Static("❯", id="prompt-symbol")
            yield PromptArea(
                id="prompt",
                placeholder="Ask Agent47 to change something…  (Enter to send)",
            )

    async def on_mount(self) -> None:
        self.topbar = self.query_one("#topbar", Static)
        self.conversation = self.query_one("#conversation", VerticalScroll)
        self.status = self.query_one("#status", Static)
        self.prompt = self.query_one("#prompt", PromptArea)

        self.topbar.update(self._topbar_text())
        self._update_status()

        await self._replay_history()

        if len(self.session.messages) > 1:
            await self._add_note(
                f"Resumed session '{self.session.id}'. {KEYS_TEXT}"
            )
        else:
            await self._add_note(
                f"Started session '{self.session.id}'. {KEYS_TEXT}"
            )

        self.prompt.focus()
        self.set_interval(0.12, self._tick)

    def _topbar_text(self) -> Text:
        text = Text()
        text.append("AGENT47", style="bold #c9d1e3")
        text.append("   ", style="")
        text.append(MODEL, style="#8b93a7")
        text.append("  ·  ", style="#4b5263")
        text.append(self.session.id, style="#8b93a7")
        return text

    def _status_text(self) -> Text:
        text = Text()

        if self._busy:
            text.append(f"{SPINNER[self._spinner_index]} ", style="#7aa2f7")
            text.append("working", style="#7aa2f7")
        else:
            text.append("● ", style="#9ece6a")
            text.append("ready", style="#9ece6a")

        text.append("  ·  ", style="#4b5263")
        text.append(
            f"ctx {fmt_tokens(self.session.last_prompt_tokens)}"
            f"/{fmt_tokens(self.context_limit)}",
            style="#7c8499",
        )

        if self.session.compactions:
            text.append("  ·  ", style="#4b5263")
            text.append(f"{self.session.compactions} compaction(s)", style="#7c8499")

        if self._busy:
            text.append("  ·  Esc cancel", style="#565f73")

        return text

    def _update_status(self) -> None:
        if hasattr(self, "status"):
            self.status.update(self._status_text())

    def _tick(self) -> None:
        if not self._busy:
            return
        self._spinner_index = (self._spinner_index + 1) % len(SPINNER)
        self._update_status()

    # -- submission / worker -------------------------------------------

    async def action_submit(self) -> None:
        if self._busy:
            await self._add_note("Still working — press Esc to cancel this turn.")
            return

        text = self.prompt.text.strip()
        if not text:
            return

        self.prompt.clear()

        if text.startswith("/"):
            await self._handle_command(text)
            return

        await self._add_message(Text(text))
        self._busy = True
        self._cancel.clear()
        self._update_status()
        self._run_turn_worker(text)

    @work(thread=True, exclusive=True, group="turn")
    def _run_turn_worker(self, text: str) -> None:
        try:
            run_turn(
                self.client,
                self.session,
                text,
                self.renderer,
                self.context_limit,
                self._cancel.is_set,
            )
        except TurnCancelled:
            self.session.save()
            self._call_ui(self._turn_cancelled)
        except Exception as exc:
            self.session.save()
            self._call_ui(self._turn_failed, str(exc))
        else:
            self.session.save()
            self._call_ui(self._turn_finished)

    @work(thread=True, exclusive=True, group="turn")
    def _run_compact_worker(self) -> None:
        try:
            compacted = compact_session(
                self.client,
                self.session,
                self.context_limit,
                self.verbose,
                self.renderer,
            )
        except Exception as exc:
            self._call_ui(self._turn_failed, f"compaction failed: {exc}")
            return

        self.session.save()
        self._call_ui(self._compact_finished, compacted)

    def _call_ui(self, callback, *args) -> None:
        try:
            self.call_from_thread(callback, *args)
        except RuntimeError:
            # The app is shutting down; nothing left to update.
            pass

    async def _turn_finished(self) -> None:
        self._busy = False
        self._cancel.clear()
        self._update_status()
        self.prompt.focus()

    async def _turn_cancelled(self) -> None:
        self._busy = False
        self._cancel.clear()
        await self._add_note("Turn cancelled — partial work was discarded.")
        self._update_status()
        self.prompt.focus()

    async def _turn_failed(self, message: str) -> None:
        self._busy = False
        self._cancel.clear()
        await self._add_note(f"Error: {message}", error=True)
        self._update_status()
        self.prompt.focus()

    async def _compact_finished(self, compacted: bool) -> None:
        self._busy = False
        if not compacted:
            await self._add_note("Nothing to compact yet.")
        self._update_status()
        self.prompt.focus()

    # -- rendering (called from the UI thread) -------------------------

    async def _add_message(self, text: Text) -> None:
        body = Text()
        body.append("you\n", style="bold #7aa2f7")
        body.append(text)
        self._scroll_to_end()
        await self.conversation.mount(Static(body, classes="user"))
        self._scroll_to_end()

    async def _add_note(self, text: str, error: bool = False) -> None:
        style = "#f7768e" if error else "#7c8499"
        classes = "note note-error" if error else "note"
        self._scroll_to_end()
        await self.conversation.mount(Static(Text(text, style=style), classes=classes))
        self._scroll_to_end()

    async def add_assistant_delta(self, chunk: str) -> None:
        if not chunk:
            return

        if self._assistant_md is None:
            self._assistant_md = Markdown("")
            await self.conversation.mount(self._assistant_md)

        self._assistant_text += chunk

        if self._render_timer is None:
            self._render_timer = self.set_timer(0.06, self._render_markdown)

    async def _render_markdown(self) -> None:
        self._render_timer = None
        if self._assistant_md is None:
            return
        await self._assistant_md.update(self._assistant_text)
        self._scroll_to_end(only_if_at_end=True)

    async def finish_assistant(self, text: str, tool_calls: bool) -> None:
        if self._render_timer is not None:
            self._render_timer.stop()
            self._render_timer = None

        final = text or self._assistant_text

        if self._assistant_md is None:
            if not final:
                return
            self._assistant_md = Markdown("")
            await self.conversation.mount(self._assistant_md)

        await self._assistant_md.update(final)
        self._assistant_text = ""
        self._assistant_md = None
        self._scroll_to_end()

    async def start_tool(self, call_id: str, name: str, arguments: str) -> None:
        summary = format_arguments(arguments)
        title = f"[bold #c9d1e3]{escape(name)}[/]"
        if summary:
            title += f"  [dim #6b7385]{escape(summary)}[/]"

        body = Static(Text("…", style="#6b7385"))
        widget = Collapsible(body, title=title, collapsed=True, classes="tool")
        self._tool_widgets[call_id] = (widget, body, summary)

        self._scroll_to_end()
        await self.conversation.mount(widget)
        self._scroll_to_end()

    async def finish_tool(self, call_id: str, name: str, content: str) -> None:
        entry = self._tool_widgets.get(call_id)
        if entry is None:
            await self.start_tool(call_id, name, "")
            entry = self._tool_widgets[call_id]

        widget, body, summary = entry
        error = content.lstrip().startswith("Error:")

        icon = "[#f7768e]✗[/]" if error else "[#9ece6a]✓[/]"
        title = f"{icon} [bold #c9d1e3]{escape(name)}[/]"
        if summary:
            title += f"  [dim #6b7385]{escape(summary)}[/]"
        widget.title = title

        body.update(Text(preview(content), style="#a9b1c3"))

        if error:
            widget.collapsed = False

        self._scroll_to_end(only_if_at_end=True)

    async def _replay_history(self) -> None:
        names: dict[str, str] = {}

        for message in self.session.messages[1:]:
            role = message.get("role")

            if role == "user":
                await self._add_message(Text(message.get("content") or ""))
            elif role == "assistant":
                if message.get("content"):
                    await self._add_assistant(message["content"])
                for call in message.get("tool_calls") or []:
                    function = call.get("function") or {}
                    call_id = call.get("id") or ""
                    name = function.get("name") or "tool"
                    names[call_id] = name
                    await self.start_tool(call_id, name, function.get("arguments") or "")
            elif role == "tool":
                call_id = message.get("tool_call_id") or ""
                await self.finish_tool(
                    call_id,
                    names.get(call_id, "tool"),
                    message.get("content") or "",
                )

    async def _add_assistant(self, text: str) -> None:
        await self.conversation.mount(Markdown(text))

    def _scroll_to_end(self, only_if_at_end: bool = False) -> None:
        if only_if_at_end and not self.conversation.is_vertical_scroll_end:
            return
        self.conversation.scroll_end(animate=False)

    # -- commands -------------------------------------------------------

    async def _handle_command(self, text: str) -> None:
        command = text.split(maxsplit=1)[0].lower()

        if command in ("/exit", "/quit"):
            await self.action_quit()
            return
        elif command == "/help":
            await self._add_note(f"{HELP_TEXT}\n\n{KEYS_TEXT}")
        elif command == "/new":
            self.session.save()
            self.session = Session(new_session_id())
            self._tool_widgets.clear()
            await self.conversation.remove_children()
            self.topbar.update(self._topbar_text())
            self._update_status()
            await self._add_note(f"Started new session '{self.session.id}'.")
        elif command == "/sessions":
            await self._show_sessions()
        elif command == "/history":
            turns = sum(
                1 for message in self.session.messages if message.get("role") == "user"
            )
            await self._add_note(
                f"session '{self.session.id}': {turns} user turn(s), "
                f"{len(self.session.messages)} messages, "
                f"{self.session.compactions} compaction(s), "
                f"last prompt {self.session.last_prompt_tokens} tokens, "
                f"{self.session.prompt_tokens} prompt / "
                f"{self.session.completion_tokens} completion tokens total"
            )
        elif command == "/compact":
            self._busy = True
            self._update_status()
            self._run_compact_worker()
        else:
            await self._add_note(f"Unknown command '{command}'. Try /help.")

        if not self._busy:
            self.prompt.focus()

    async def _show_sessions(self) -> None:
        entries = collect_sessions()
        if not entries:
            await self._add_note("No saved sessions yet.")
            return

        lines = ["Saved sessions:"]
        for entry in entries:
            if entry.get("unreadable"):
                lines.append(f"  {entry['id']}  (unreadable)")
                continue
            marker = "*" if entry["id"] == self.session.id else " "
            lines.append(
                f" {marker} {entry['id']}  {entry['updated_at']}  "
                f"({entry['messages']} messages, {entry['compactions']} compactions)"
            )

        await self._add_note("\n".join(lines))

    # -- key actions ----------------------------------------------------

    def action_cancel_turn(self) -> None:
        if self._busy:
            self._cancel.set()
            self.status.update(
                Text(
                    f"{SPINNER[self._spinner_index]} cancelling… "
                    "(finishing the current request)",
                    style="#e0af68",
                )
            )

    async def action_clear_view(self) -> None:
        await self.conversation.remove_children()
        self._tool_widgets.clear()
        await self._add_note(
            f"View cleared. Session '{self.session.id}' is still loaded."
        )

    async def action_quit(self) -> None:
        self._cancel.set()
        try:
            self.session.save()
        except OSError:
            pass
        self.exit()


class TuiRenderer(Renderer):
    """Bridge between the worker thread and the Textual app."""

    live = True

    def __init__(self, app: Agent47App) -> None:
        self.app = app
        self.verbose = app.verbose
        self._buffer: list[str] = []
        self._last_flush = 0.0

    def _ui(self, callback, *args) -> None:
        try:
            self.app.call_from_thread(callback, *args)
        except RuntimeError:
            pass

    def _flush(self) -> None:
        if not self._buffer:
            return
        chunk = "".join(self._buffer)
        self._buffer.clear()
        self._last_flush = time.monotonic()
        self._ui(self.app.add_assistant_delta, chunk)

    def assistant_delta(self, text: str) -> None:
        if not text:
            return
        self._buffer.append(text)
        if time.monotonic() - self._last_flush >= 0.05:
            self._flush()

    def assistant_end(self, text: str, tool_calls: bool = False) -> None:
        self._flush()
        self._ui(self.app.finish_assistant, text, tool_calls)

    def tool_call(self, call_id: str, name: str, arguments: str) -> None:
        self._ui(self.app.start_tool, call_id, name, arguments)

    def tool_result(self, call_id: str, name: str, content: str) -> None:
        self._ui(self.app.finish_tool, call_id, name, content)

    def tokens(
        self,
        prompt: int | None,
        completion: int | None,
        limit: int,
        estimated: bool = False,
    ) -> None:
        self._ui(self.app._update_status)

    def compaction(self, before: int, after: int, summarized: int, kept: int) -> None:
        self._ui(
            self.app._add_note,
            f"Compacted {before} → {after} est. tokens "
            f"({summarized} messages summarized, {kept} kept).",
        )

    def notice(self, text: str) -> None:
        self._ui(self.app._add_note, text)

    def error(self, text: str) -> None:
        self._ui(self.app._add_note, f"Error: {text}", True)
