import argparse
import os
import re
from urllib.parse import urlparse

from openai import OpenAI

from call_function import available_functions, call_function
from config import BASE_URL, CONTEXT_TOKEN_LIMIT, MAX_ITERATIONS, MODEL
from session import (
    Session,
    list_sessions,
    load_session,
    maybe_compact,
    new_session_id,
    truncate_tool_result,
)

SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

HELP_TEXT = """Commands:
  /help          Show this help
  /new           Save this session and start a new one
  /sessions      List saved sessions
  /history       Show this session's turn, message, token, and compaction counts
  /exit, /quit   Save and exit (Ctrl-D also works)

Anything else is sent to the agent."""


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


def assistant_message_to_dict(message) -> dict:
    """Convert the SDK message object into a plain, JSON-serializable dict."""
    data = {"role": "assistant", "content": message.content}

    if message.tool_calls:
        data["tool_calls"] = [
            tool_call.model_dump(exclude_none=True) for tool_call in message.tool_calls
        ]

    if message.refusal:
        data["refusal"] = message.refusal

    return data


def run_turn(
    client: OpenAI,
    session: Session,
    user_input: str,
    verbose: bool = False,
    context_limit: int = CONTEXT_TOKEN_LIMIT,
) -> bool:
    """Run the agent loop for one user message. Returns False on iteration limit."""
    user_message = {"role": "user", "content": user_input}
    session.messages.append(user_message)

    try:
        for _ in range(MAX_ITERATIONS):
            maybe_compact(client, session, context_limit, verbose)

            response = client.chat.completions.create(
                model=MODEL,
                messages=session.messages,
                # temperature=0
                tools=available_functions,
            )

            if response.usage is None:
                if verbose:
                    print("[tokens] provider did not return usage")
            else:
                session.prompt_tokens += response.usage.prompt_tokens
                session.completion_tokens += response.usage.completion_tokens
                session.last_prompt_tokens = response.usage.prompt_tokens

                if verbose:
                    print(
                        f"[tokens] prompt={response.usage.prompt_tokens} "
                        f"completion={response.usage.completion_tokens} "
                        f"(limit {context_limit})"
                    )

            message = response.choices[0].message
            session.messages.append(assistant_message_to_dict(message))

            if not message.tool_calls:
                print(f"\n{message.content or '(no response)'}\n")
                return True

            for tool_call in message.tool_calls:
                result_message = call_function(tool_call, verbose)
                content = truncate_tool_result(result_message["content"])

                if not content:
                    raise RuntimeError("Tool call returned empty content")

                result_message["content"] = content

                if verbose:
                    print(f"-> {content}")

                session.messages.append(result_message)

        print(f"Agent stopped: reached the maximum of {MAX_ITERATIONS} iterations for this turn.")
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


def run_interactive(client: OpenAI, args: argparse.Namespace) -> None:
    session = load_session(args.session) if args.session else Session(new_session_id())

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

                if command in ("/exit", "/quit"):
                    break
                if command == "/help":
                    print(HELP_TEXT)
                elif command == "/new":
                    session.save()
                    session = Session(new_session_id())
                    print(f"Started new session '{session.id}'.\n")
                elif command == "/sessions":
                    list_sessions()
                elif command == "/history":
                    turns = sum(1 for m in session.messages if m.get("role") == "user")
                    print(
                        f"session '{session.id}': {turns} user turn(s), "
                        f"{len(session.messages)} messages, "
                        f"{session.compactions} compaction(s), "
                        f"last prompt {session.last_prompt_tokens} tokens, "
                        f"{session.prompt_tokens} prompt / "
                        f"{session.completion_tokens} completion tokens total"
                    )
                else:
                    print(f"Unknown command '{command}'. Try /help.")
                continue

            try:
                run_turn(client, session, user_input, args.verbose, args.context_limit)
            except KeyboardInterrupt:
                print("\nInterrupted - this turn was discarded.\n")
            except Exception as exc:
                print(f"\nError: {exc}\n")

            session.save()
    finally:
        session.save()
        print(f"Session saved. Resume with: uv run main.py --session {session.id}")


def run_once(client: OpenAI, args: argparse.Namespace) -> None:
    session = load_session(args.session) if args.session else Session(new_session_id())

    try:
        completed = run_turn(
            client, session, args.user_prompt, args.verbose, args.context_limit
        )
    except KeyboardInterrupt:
        print("\nInterrupted.")
        raise SystemExit(130)
    except Exception as exc:
        print(f"Error: {exc}")
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
    args = parser.parse_args()

    if args.session and not SESSION_ID_PATTERN.match(args.session):
        parser.error(
            "session names may only contain letters, numbers, dots, dashes, and underscores"
        )

    if args.context_limit < 1000:
        parser.error("--context-limit must be at least 1000 tokens")

    client = build_client()

    if args.user_prompt:
        run_once(client, args)
    else:
        run_interactive(client, args)


if __name__ == "__main__":
    main()
