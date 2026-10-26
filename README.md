# Agent47

Agent47 is a small, autonomous coding agent with a full-screen terminal interface. It uses any OpenAI-compatible model (OpenRouter, Ollama, LM Studio, vLLM, …) to inspect a project, read and write files, execute Python code, and iterate until a coding task is complete.

It is a learning project and a toy version of tools like Claude Code or Cursor's agent mode — **not** production software.

## How it works

The agent runs a tool-calling loop:

1. The system prompt and user request are sent to the model.
2. The model chooses one or more tools to call.
3. Agent47 executes each tool, appends the result to the conversation, and sends it back.
4. The loop stops when the model returns a text response or after 20 iterations.
5. In interactive mode the conversation is kept, so follow-up messages have full context, and it is saved to a session file you can resume later.
6. When the conversation grows past a token budget, older turns are summarized and replaced by that summary, so sessions can continue indefinitely.

### Tools

| Tool | Purpose |
| --- | --- |
| `get_files_info` | List files and directories inside the working directory |
| `get_file_content` | Read a file (truncated at 10,000 characters) |
| `run_python_file` | Run a Python file with optional arguments (30s timeout) |
| `write_file` | Write or overwrite a file |

All file paths are resolved relative to the working directory and are validated to prevent escaping it. The agent uses the bundled `calculator/` project as its working directory.

## Requirements

- Python 3.13+
- [uv](https://docs.astral.sh/uv/) (recommended) or pip
- An [OpenRouter](https://openrouter.ai/) API key, or a local OpenAI-compatible server (see [Local models](#local-models))

## Setup

```bash
git clone https://github.com/irfanekon04/agent47.git
cd agent47
uv sync
```

Create a `.env` file in the project root:

```bash
OPENROUTER_API_KEY=your_key_here
```

You can copy the provided template:

```bash
cp .env.example .env
```

The provider is configurable. These optional variables override the OpenRouter defaults:

| Variable | Default | Purpose |
| --- | --- | --- |
| `AGENT47_BASE_URL` | `https://openrouter.ai/api/v1` | Any OpenAI-compatible API endpoint |
| `AGENT47_MODEL` | `openrouter/free` | Model ID sent to the provider |
| `AGENT47_API_KEY` | falls back to `OPENROUTER_API_KEY` | API key; placeholders are fine for local servers |

## Usage

Start the full-screen interface:

```bash
uv run main.py
```

The TUI keeps the transcript in a scrollable view, streams assistant text as it arrives, and renders each tool call as a collapsible `✓`/`✗` row. The status bar shows the model, session, context usage, and compaction count.

| Key | Action |
| --- | --- |
| `Enter` | Send the message |
| `Ctrl+J` | Insert a newline |
| `Esc` | Cancel the turn in progress |
| `Ctrl+L` | Clear the transcript view (keeps the session) |
| `Ctrl+Q` | Save and quit |

In-session commands: `/help`, `/new`, `/sessions`, `/history`, `/compact`, `/exit`.

Resume a session later with `--session <name-or-id>`:

```bash
uv run main.py --session 20261026-143000
```

A named session is created on first use, so you can keep returning to the same conversation:

```bash
uv run main.py --session calculator-refactor
```

### Plain mode

The original line-based interface is still available with `--plain`. It is also used automatically when the input or output is not a terminal, so piping and scripts keep working:

```bash
uv run main.py --plain
```

Both interfaces share the same session files, so you can switch between them freely:

```
Agent47 - Resumed session 'calculator-refactor' (model: openrouter/free @ https://openrouter.ai/api/v1)
Compaction triggers at ~16000 prompt tokens. Type /help for commands, /exit to quit.

>> There is a bug in the calculator. Find it, fix it, and verify the tests pass.
...
>> /exit
Session saved. Resume with: uv run main.py --session calculator-refactor
```

### One-shot mode

Pass a prompt and the agent exits when it is done:

```bash
uv run main.py "Fix the bug in calculator/pkg/calculator.py"
```

Add `--verbose` to show token usage, tool arguments, and tool output in plain and one-shot mode:

```bash
uv run main.py --verbose
uv run main.py "Run the calculator tests and fix any failures" --verbose
```

Responses stream by default. Pass `--no-stream` (or set `AGENT47_STREAM=0`) to wait for complete responses instead; this is useful for servers with broken streaming support.

Sessions are stored as JSON in `.agent47/sessions/` (gitignored), including the full message history, summary, and token usage. Interrupted turns are discarded so the saved history stays valid.

## Context compaction

Long sessions eventually exceed the model's context window. Agent47 handles that by compacting:

1. Before each model call it checks the last measured prompt size and a cheap character-based estimate.
2. Once the context limit is reached, it summarizes older turns with the model (goals, files changed, tests run, unresolved issues) and stores that summary in the session.
3. The summary is injected into the system prompt, and the most recent turns (up to roughly half the context limit, capped at 6k tokens) are kept verbatim.
4. Cuts only happen at user-message boundaries, so `assistant(tool_calls)` / `tool` pairs are never split. Individual tool results are also truncated at 12k characters.
5. If summarization fails, a deterministic summary of user requests and tool calls is used instead.

Tune the trigger with `--context-limit` (default 16000, minimum 1000):

```bash
uv run main.py --session big-refactor --context-limit 32000
```

Set the limit below the model's context window and leave room for the response. The default of 16000 is conservative for most OpenRouter models; lower it if the free route picks a model with a small window.

Compaction appears as a notice in the transcript, and `/history` reports the count. In plain mode it looks like this:

```
[compacted 18432 -> 6120 est. tokens: summarized 24 messages, kept 6]
```

Tune the defaults in `config.py` (`KEEP_RECENT_TOKENS`, `SUMMARY_MAX_CHARS`, `TRANSCRIPT_MAX_CHARS`, `MAX_TOOL_RESULT_CHARS`).

The model defaults to the free `openrouter/free` route. Set `AGENT47_MODEL` to use a specific model, or edit the defaults in `config.py`.

## Local models

Agent47 uses the OpenAI chat-completions format, so any OpenAI-compatible server works: Ollama, LM Studio, llama.cpp, vLLM, etc. Point the three `AGENT47_*` variables at it and nothing else changes:

```bash
AGENT47_BASE_URL=http://localhost:11434/v1
AGENT47_MODEL=ornith:9b
AGENT47_API_KEY=local
```

`AGENT47_API_KEY` is required by the OpenAI client but ignored by local servers. If the base URL is localhost, a placeholder is enough.

### Ollama

```bash
brew install ollama
OLLAMA_CONTEXT_LENGTH=32768 OLLAMA_FLASH_ATTENTION=1 OLLAMA_KV_CACHE_TYPE=q8_0 ollama serve
ollama pull ornith:9b                      # in another terminal

# .env
AGENT47_BASE_URL=http://localhost:11434/v1
AGENT47_MODEL=ornith:9b
AGENT47_API_KEY=local

uv run main.py --session local --context-limit 16000
```

The context-length step matters: Ollama defaults to **4k context when VRAM is under 24 GiB**, which is too small for the system prompt plus a few tool results. Check what was actually allocated with `ollama ps` (the `CONTEXT` column), and keep Agent47's `--context-limit` below it so there is room for the response. Flash attention plus a quantized KV cache keep the context affordable on 16 GB.

If you prefer to run Ollama as a login service (`brew services start ollama`), shell environment variables are not applied. Bake the context into a copy of the model instead:

```bash
cat > Modelfile <<'EOF'
FROM ornith:9b
PARAMETER num_ctx 32768
EOF
ollama create ornith-32k -f Modelfile
```

Then use `AGENT47_MODEL=ornith-32k`.

### Picking a model for 16 GB Apple Silicon

| Model | Download | Notes |
| --- | --- | --- |
| `ornith:9b` | 5.6 GB | Coding-agent specialist (trained for terminal/SWE-style tasks), tools, 256K context. Best fit for this agent on 16 GB. |
| `qwen3.5:9b` | 6.6–7.6 GB | Newest Qwen general model, tools/thinking, 256K context. Better all-rounder and more established tool-calling family. |
| `gemma4:12b` | 7.7–8.0 GB | Newest Gemma, native function calling, native system prompt, 256K context. Strong general reasoning; less coding-agent-specific than the two above. |
| `gemma4:e4b` | 6.6–9.5 GB | Gemma 4 edge variant. Tools and 128K context, but noticeably weaker coding/agent benchmarks. |
| `gpt-oss:20b` | 14 GB | Strong reasoning and native function calling, but very tight on 16 GB. Expect a smaller usable context and possible swapping. |
| `devstral-small-2` | 15 GB | Excellent agentic coder, but effectively too large for a 16 GB machine once context is allocated. |
| `qwen3-coder-next` | 52 GB | Great agentic coder, needs ~64 GB. Not viable here. |

Avoid **Gemma 3** and **Gemma 3n** for this agent: Ollama's builds do not advertise native tool calling, Gemma 3n is limited to a 32K context, and Gemma 3 predates Gemma 4's native system-prompt support. A coding agent needs all three.

For this project I'd start with **`ornith:9b`**, then try **`qwen3.5:9b`** or **`gemma4:12b`** if you want a newer general model. Use `gpt-oss:20b` only if you close other apps and can live with a small context. Avoid the 20B+ dense models on 16 GB.

LM Studio works the same way: start its local server (default `http://localhost:1234/v1`), copy the model identifier from the server tab, and use it as `AGENT47_MODEL`. MLX builds are usually the fastest option on Apple Silicon.

Local models are convenient, private, and free to run, but a 9B model is still weaker than a frontier cloud model at long multi-step coding tasks. Expect more retries and occasional malformed tool calls; invalid JSON is returned to the model as a tool error so it can recover instead of crashing the turn.

Some thinking models occasionally finish a turn with only reasoning and no final text. Agent47 retries once and never stores an empty assistant message. It also converts `null` message content to an empty string before every request, because strict servers such as Ollama reject `content: null` with `invalid message content type: <nil>`.

## Example

```bash
uv run main.py "There is a bug in the calculator. Find it, fix it, and verify the tests pass."
```

The agent inspects the free-form calculator app, edits the broken source file, runs `calculator/tests.py`, and reports what it changed.

## Project structure

```
agent47/
├── main.py                 # CLI entry point, agent loop, and command handling
├── tui.py                  # Full-screen Textual interface
├── renderer.py             # Agent events and the plain-text renderer
├── providers.py            # Streaming chat completions and fallbacks
├── session.py              # Session persistence and context compaction
├── call_function.py        # Tool dispatch
├── prompts.py              # System prompt
├── config.py               # Provider, model, limits, and compaction settings
├── functions/              # Tool implementations and JSON schemas
├── calculator/             # Example project the agent operates on
├── test_*.py               # Offline smoke tests (tools, providers, sessions, TUI)
└── pyproject.toml
```

## Safety notes

This agent can read, overwrite, and execute files in its working directory. Only run it on code you are willing to modify, and commit your changes before letting it loose so you can revert. Do not point it at directories containing secrets.

The tool sandbox only blocks paths outside the configured working directory. It does not sandbox the Python interpreter itself, so executed code has the same permissions as your user.

## License

MIT
