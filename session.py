import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from openai import OpenAI

from config import (
    CONTEXT_TOKEN_LIMIT,
    KEEP_RECENT_TOKENS,
    MAX_TOOL_RESULT_CHARS,
    MODEL,
    SUMMARY_MAX_CHARS,
    TRANSCRIPT_MAX_CHARS,
)
from prompts import system_prompt

SESSION_DIR = Path(".agent47/sessions")

SUMMARY_PROMPT = """You are compacting the history of an AI coding agent so it can continue \
working in the same session.

Summarize the transcript below in concise bullet points. Preserve:
- The user's goals, preferences, and constraints.
- What was inspected, changed, or created, with exact file paths.
- Commands and tests run, and their outcomes.
- Bugs found, decisions made, and anything still broken or TODO.
- Facts the agent must not rediscover.

Do not invent anything. Do not include generic advice. Aim for at most 400 words."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_session_id() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


@dataclass
class Session:
    id: str
    messages: list[dict] = field(default_factory=list)
    summary: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    last_prompt_tokens: int = 0
    compactions: int = 0
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        self.refresh_system_message()

    def system_content(self) -> str:
        if not self.summary:
            return system_prompt

        return (
            f"{system_prompt}\n\n"
            "## Summary of earlier conversation\n\n"
            f"{self.summary}"
        )

    def refresh_system_message(self) -> None:
        content = self.system_content()

        if self.messages and self.messages[0].get("role") == "system":
            self.messages[0]["content"] = content
        else:
            self.messages.insert(0, {"role": "system", "content": content})

    @property
    def path(self) -> Path:
        return SESSION_DIR / f"{self.id}.json"

    def save(self) -> None:
        SESSION_DIR.mkdir(parents=True, exist_ok=True)
        self.updated_at = utc_now()

        payload = {
            "id": self.id,
            "model": MODEL,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "summary": self.summary,
            "compactions": self.compactions,
            "usage": {
                "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens,
                "last_prompt_tokens": self.last_prompt_tokens,
            },
            "messages": self.messages,
        }

        temp_path = self.path.with_suffix(".json.tmp")
        temp_path.write_text(json.dumps(payload, indent=2))
        temp_path.replace(self.path)


def load_session(session_id: str) -> Session:
    path = SESSION_DIR / f"{session_id}.json"

    if not path.exists():
        return Session(session_id)

    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Could not read session '{session_id}' ({exc}); starting a new one.")
        return Session(session_id)

    usage = data.get("usage") or {}
    session = Session(
        session_id,
        messages=data.get("messages") or [],
        summary=data.get("summary") or "",
        prompt_tokens=usage.get("prompt_tokens", 0),
        completion_tokens=usage.get("completion_tokens", 0),
        last_prompt_tokens=usage.get("last_prompt_tokens", 0),
        compactions=data.get("compactions", 0),
    )
    session.created_at = data.get("created_at", session.created_at)
    return session


def collect_sessions() -> list[dict]:
    """Return saved sessions as plain dicts, sorted by session name."""
    if not SESSION_DIR.exists():
        return []

    sessions = []
    for path in sorted(SESSION_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            sessions.append({"id": path.stem, "unreadable": True})
            continue

        sessions.append(
            {
                "id": path.stem,
                "updated_at": data.get("updated_at", "unknown"),
                "messages": len(data.get("messages") or []),
                "compactions": data.get("compactions", 0),
                "model": data.get("model", ""),
            }
        )

    return sessions


def list_sessions() -> None:
    sessions = collect_sessions()
    if not sessions:
        print("No saved sessions yet.")
        return

    for entry in sessions:
        if entry.get("unreadable"):
            print(f"- {entry['id']} (unreadable)")
            continue

        print(
            f"- {entry['id']}  updated {entry['updated_at']}  "
            f"({entry['messages']} messages, {entry['compactions']} compactions)"
        )


def estimate_tokens(messages: list[dict]) -> int:
    """Cheap token estimate: roughly four characters per token of JSON payload."""
    payload = json.dumps(messages, ensure_ascii=False)
    return max(1, len(payload) // 4)


def needs_compaction(session: Session, context_limit: int) -> bool:
    measured = session.last_prompt_tokens
    estimated = estimate_tokens(session.messages)
    return max(measured, estimated) >= context_limit


def find_cut_index(
    messages: list[dict], keep_recent_tokens: int = KEEP_RECENT_TOKENS
) -> int | None:
    """Return the index of the oldest user message to keep, or None if nothing can go."""
    user_indices = [i for i, message in enumerate(messages) if message.get("role") == "user"]

    if len(user_indices) < 2:
        return None

    cut = user_indices[-1]

    # Walk backwards through complete turns while they fit the recent-turn budget.
    for index in reversed(user_indices[:-1]):
        if estimate_tokens(messages[index:]) > keep_recent_tokens:
            break
        cut = index

    # index 1 is the first real message; cutting there would summarize nothing.
    if cut <= 1:
        return None

    return cut


def clip(text: str, limit: int = 2000) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "... [truncated]"


def make_transcript(messages: list[dict]) -> str:
    tool_names: dict[str, str] = {}
    lines: list[str] = []

    for message in messages:
        role = message.get("role")

        if role == "user":
            lines.append(f"USER: {clip(message.get('content') or '')}")
        elif role == "assistant":
            for tool_call in message.get("tool_calls") or []:
                function = tool_call.get("function") or {}
                tool_names[tool_call.get("id", "")] = function.get("name", "tool")
                lines.append(
                    f"ASSISTANT called {function.get('name', 'tool')}"
                    f"({clip(function.get('arguments') or '', 500)})"
                )
            if message.get("content"):
                lines.append(f"ASSISTANT: {clip(message['content'])}")
        elif role == "tool":
            name = tool_names.get(message.get("tool_call_id", ""), "tool")
            lines.append(f"TOOL {name} result: {clip(message.get('content') or '')}")

    transcript = "\n".join(lines)

    if len(transcript) > TRANSCRIPT_MAX_CHARS:
        transcript = (
            "... [earlier transcript truncated] ...\n"
            + transcript[-TRANSCRIPT_MAX_CHARS:]
        )

    return transcript


def fallback_summary(messages: list[dict]) -> str:
    """Deterministic summary used when the summarization call fails."""
    lines = ["(Automatic summary: model summarization failed.)"]

    for message in messages:
        role = message.get("role")
        if role == "user":
            lines.append(f"- User asked: {clip(message.get('content') or '', 300)}")
        elif role == "assistant":
            for tool_call in message.get("tool_calls") or []:
                function = tool_call.get("function") or {}
                lines.append(
                    f"- Ran {function.get('name', 'tool')}"
                    f"({clip(function.get('arguments') or '', 200)})"
                )
            if message.get("content"):
                lines.append(f"- Agent: {clip(message['content'], 300)}")

    summary = "\n".join(lines)
    return summary[-SUMMARY_MAX_CHARS:]


def summarize(client: OpenAI, session: Session, previous_summary: str, messages: list[dict]) -> str:
    transcript = make_transcript(messages)
    parts: list[str] = []

    if previous_summary:
        parts.append(f"Summary of even earlier conversation:\n{previous_summary}")
    parts.append(f"Conversation to summarize:\n{transcript}")

    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": SUMMARY_PROMPT},
            {"role": "user", "content": "\n\n".join(parts)},
        ],
    )

    if response.usage is not None:
        session.prompt_tokens += response.usage.prompt_tokens
        session.completion_tokens += response.usage.completion_tokens

    summary = (response.choices[0].message.content or "").strip()
    if not summary:
        raise RuntimeError("summarizer returned an empty summary")

    return summary[:SUMMARY_MAX_CHARS]


def compact_session(
    client: OpenAI,
    session: Session,
    context_limit: int = CONTEXT_TOKEN_LIMIT,
    verbose: bool = False,
    renderer=None,
) -> bool:
    """Summarize older turns into one persistent summary. Returns True if compacted."""
    # Never try to keep more than half the context verbatim, otherwise a low
    # context limit can leave nothing safe to summarize away.
    keep_budget = max(500, min(KEEP_RECENT_TOKENS, context_limit // 2))
    cut = find_cut_index(session.messages, keep_budget)

    if cut is None:
        return False

    older = session.messages[1:cut]
    recent = session.messages[cut:]
    previous_tokens = estimate_tokens(session.messages)

    try:
        summary = summarize(client, session, session.summary, older)
    except Exception as exc:
        if renderer is not None:
            renderer.notice(
                f"Summarization failed ({exc}); using a fallback summary."
            )
        elif verbose:
            print(f"[compaction] summarization failed: {exc}")
        summary = fallback_summary(older)

    session.summary = summary
    session.messages = [session.messages[0], *recent]
    session.refresh_system_message()
    session.last_prompt_tokens = 0
    session.compactions += 1

    after_tokens = estimate_tokens(session.messages)
    if renderer is not None:
        renderer.compaction(previous_tokens, after_tokens, len(older), len(recent))
    else:
        print(
            f"[compacted {previous_tokens} -> {after_tokens} est. tokens: "
            f"summarized {len(older)} messages, kept {len(recent)}]"
        )

    return True


def maybe_compact(
    client: OpenAI,
    session: Session,
    context_limit: int = CONTEXT_TOKEN_LIMIT,
    verbose: bool = False,
    renderer=None,
) -> bool:
    if not needs_compaction(session, context_limit):
        return False
    return compact_session(client, session, context_limit, verbose, renderer)


def truncate_tool_result(content: str) -> str:
    if len(content) <= MAX_TOOL_RESULT_CHARS:
        return content

    omitted = len(content) - MAX_TOOL_RESULT_CHARS
    return f"{content[:MAX_TOOL_RESULT_CHARS]}\n... [truncated {omitted} characters]"
