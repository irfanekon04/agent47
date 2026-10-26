import argparse
import os
import re
import sys
from urllib.parse import urlparse

from openai import OpenAI

import providers
from call_function import available_functions, call_function
from config import BASE_URL, CONTEXT_TOKEN_LIMIT, MAX_ITERATIONS, MODEL
from errors import TurnCancelled
from providers import complete
from renderer import HELP_TEXT, PlainRenderer
from session import (
    Session,
    compact_session,
    estimate_tokens,
    list_sessions,
    load_session,
    maybe_compact,
    new_session_id,
    truncate_tool_result,
)

SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def build_client() -> OpenAI:
    api_key = os.environ.get("AGENT47_API_KEY") or os.environ.get("OPENROUTER_API_KEY")
    host = urlparse(BASE_URL).hostname or ""

    if not api_key:
        if host in {"localhost", "127.0.0.1", "0.0.0.0", "::1"}:
            api_key = "local"
        else:
            raise RuntimeError(
                "No API key found. Set AGENT47_API_KEY or OPENROUTER_API_KEY."
            )

    return OpenAI(
        base_url=BASE_URL,
        api_key=api_key,
    )


def run_turn(
    client: OpenAI,
    session: Session,
    user_input: str,
    renderer=None,
    context_limit: int = CONTEXT_TOKEN_LIMIT,
    should_stop=None,
) -> bool:
    """Run the agent loop for one user message. Returns False on iteration limit."""
    renderer = renderer or PlainRenderer()
    user_message = {"role": "user", "content": user_input}
    session.messages.append(user_message)
    empty_retries = 0

    try:
        for _ in range(MAX_ITERATIONS):
            if should_stop is not None and should_stop():
                raise TurnCancelled()

            maybe_compact(
                client,
                session,
                context_limit,
                renderer.verbose,
                renderer,
            )

            prompt_estimate = estimate_tokens(session.messages)

            completion = complete(
                client,
                session.messages,
                available_functions,
                on_delta=renderer.assistant_delta,
            )

            if completion.prompt_tokens is None:
                session.prompt_tokens += prompt_estimate
                session.last_prompt_tokens = prompt_estimate
                estimated = True
            else:
                session.prompt_tokens += completion.prompt_tokens
                session.completion_tokens += completion.completion_tokens or 0
                session.last_prompt_tokens = completion.prompt_tokens
                estimated = False

            message = completion.message
            content = message.get("content") or ""
            tool_calls = message.get("tool_calls") or []

            # Some local models occasionally finish a turn with only reasoning
            # and no answer. Retrying once usually produces a real response;
            # never store the empty assistant message, because strict servers
            # reject it on the next request.
            if not content.strip() and not tool_calls:
                renderer.tokens(
                    session.last_prompt_tokens,
                    completion.completion_tokens,
                    context_limit,
                    estimated,
                )
                if empty_retries == 0:
                    empty_retries += 1
                    renderer.notice("The model returned an empty response; retrying once.")
                    continue
                renderer.notice(
                    "The model returned another empty response. "
                    "Try rephrasing the request or switching models."
                )
                return True

            session.messages.append(message)
            renderer.assistant_end(content, tool_calls=bool(tool_calls))
            renderer.tokens(
                session.last_prompt_tokens,
                completion.completion_tokens,
                context_limit,
                estimated,
            )

            if not tool_calls:
                return True

            for tool_call in tool_calls:
                if should_stop is not None and should_stop():
                    raise TurnCancelled()

                function = tool_call.get("function") or {}
                name = function.get("name") or "tool"
                arguments = function.get("arguments") or "{}"
                call_id = tool_call.get("id") or ""

                renderer.tool_call(call_id, name, arguments)

                result_message = call_function(name, arguments, call_id)
                content = truncate_tool_result(str(result_message.get("content") or ""))

                if not content:
                    raise RuntimeError("Tool call returned empty content")

                result_message["content"] = content
                renderer.tool_result(call_id, name, content)

                session.messages.append(result_message)

        renderer.notice(
            f"Agent stopped: reached the maximum of {MAX_ITERATIONS} "
            "iterations for this turn."
        )
        return False

    except BaseException:
        # Drop the partial turn so the saved history stays valid for the next
        # request. Find the message by identity because compaction can move it.
        index = next(
            (i for i, message in enumerate(session.messages) if message is user_message),
            len(session.messages),
        )
        del session.messages[index:]
        session.last_prompt_tokens = 0
        raise


def handle_command(
    command: str,
    client: OpenAI,
    session: Session,
    args: argparse.Namespace,
    renderer,
) -> tuple[Session, bool]:
    """Handle a slash command. Returns the (possibly new) session and exit flag."""
    if command in ("/exit", "/quit"):
        return session, True

    if command == "/help":
        renderer.notice(HELP_TEXT)
    elif command == "/new":
        session.save()
        session = Session(new_session_id())
        renderer.notice(f"Started new session '{session.id}'.")
    elif command == "/sessions":
        list_sessions()
    elif command == "/history":
        turns = sum(1 for m in session.messages if m.get("role") == "user")
        renderer.notice(
            f"session '{session.id}': {turns} user turn(s), "
            f"{len(session.messages)} messages, "
            f"{session.compactions} compaction(s), "
            f"last prompt {session.last_prompt_tokens} tokens, "
            f"{session.prompt_tokens} prompt / "
            f"{session.completion_tokens} completion tokens total"
        )
    elif command == "/compact":
        try:
            compacted = compact_session(
                client,
                session,
                args.context_limit,
                args.verbose,
                renderer,
            )
        except Exception as exc:
            renderer.error(f"compaction failed: {exc}")
        else:
            if not compacted:
                renderer.notice("Nothing to compact yet.")
    else:
        renderer.notice(f"Unknown command '{command}'. Try /help.")

    return session, False


def run_plain_interactive(client: OpenAI, args: argparse.Namespace) -> None:
    session = load_session(args.session) if args.session else Session(new_session_id())
    renderer = PlainRenderer(args.verbose)

    resumed = bool(args.session) and session.path.exists()
    action = "Resumed" if resumed else "Started"
    print(f"Agent47 - {action} session '{session.id}' (model: {MODEL} @ {BASE_URL})")
    print(
        f"Compaction triggers at ~{args.context_limit} prompt tokens. "
        "Type /help for commands, /exit to quit.\n"
    )

    try:
        while True:
            try:
                user_input = input(">> ").strip()
            except EOFError:
                print()
                break
            except KeyboardInterrupt:
                print()
                continue

            if not user_input:
                continue

            if user_input.startswith("/"):
                command = user_input.split(maxsplit=1)[0].lower()
                session, should_exit = handle_command(
                    command, client, session, args, renderer
                )
                if should_exit:
                    break
                continue

            try:
                run_turn(
                    client,
                    session,
                    user_input,
                    renderer,
                    args.context_limit,
                )
            except KeyboardInterrupt:
                print("\nInterrupted - this turn was discarded.\n")
            except Exception as exc:
                renderer.error(str(exc))

            session.save()
    finally:
        session.save()
        print(f"Session saved. Resume with: uv run main.py --session {session.id}")


def run_tui(client: OpenAI, args: argparse.Namespace) -> None:
    from tui import Agent47App

    session = load_session(args.session) if args.session else Session(new_session_id())
    app = Agent47App(
        client=client,
        session=session,
        context_limit=args.context_limit,
        verbose=args.verbose,
    )
    app.run()


def run_interactive(client: OpenAI, args: argparse.Namespace) -> None:
    use_tui = (
        not args.plain
        and sys.stdin.isatty()
        and sys.stdout.isatty()
    )

    if use_tui:
        try:
            run_tui(client, args)
            return
        except ImportError:
            print("Textual is not installed; falling back to plain mode.")
        except Exception as exc:
            print(f"Could not start the TUI: {exc}")
            print("Re-run with --plain to use the line-based interface.")
            raise SystemExit(1)

    run_plain_interactive(client, args)


def run_once(client: OpenAI, args: argparse.Namespace) -> None:
    session = load_session(args.session) if args.session else Session(new_session_id())
    renderer = PlainRenderer(args.verbose)

    try:
        completed = run_turn(
            client, session, args.user_prompt, renderer, args.context_limit
        )
    except KeyboardInterrupt:
        print("\nInterrupted.")
        raise SystemExit(130)
    except Exception as exc:
        renderer.error(str(exc))
        raise SystemExit(1)

    if args.session:
        session.save()

    if not completed:
        raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Agent47")
    parser.add_argument(
        "user_prompt",
        nargs="?",
        help="One-shot prompt. Omit it to start an interactive session.",
    )
    parser.add_argument(
        "--session",
        "-s",
        help="Session name to resume, or create if it does not exist",
    )
    parser.add_argument(
        "--context-limit",
        type=int,
        default=CONTEXT_TOKEN_LIMIT,
        metavar="TOKENS",
        help=(
            "Compact the conversation when the prompt reaches this many tokens "
            f"(default: {CONTEXT_TOKEN_LIMIT})"
        ),
    )
    parser.add_argument("--verbose", action="store_true", help="Enable verbose output")
    parser.add_argument(
        "--plain",
        action="store_true",
        help="Use the line-based interface instead of the full-screen TUI",
    )
    parser.add_argument(
        "--no-stream",
        action="store_true",
        help="Wait for complete responses instead of streaming tokens",
    )
    args = parser.parse_args()

    if args.session and not SESSION_ID_PATTERN.match(args.session):
        parser.error(
            "session names may only contain letters, numbers, dots, dashes, and underscores"
        )

    if args.context_limit < 1000:
        parser.error("--context-limit must be at least 1000 tokens")

    if args.no_stream:
        providers.STREAM_RESPONSES = False

    client = build_client()

    if args.user_prompt:
        run_once(client, args)
    else:
        run_interactive(client, args)


if __name__ == "__main__":
    main()
