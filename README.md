# Agent47

Agent47 is a small, autonomous coding agent that runs in your terminal. It uses an LLM through OpenRouter to inspect a project, read and write files, execute Python code, and iterate until a coding task is complete.

It is a learning project and a toy version of tools like Claude Code or Cursor's agent mode — **not** production software.

## How it works

The agent runs a tool-calling loop:

1. The system prompt and user request are sent to the model.
2. The model chooses one or more tools to call.
3. Agent47 executes each tool, appends the result to the conversation, and sends it back.
4. The loop stops when the model returns a text response or after 20 iterations.

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
- An [OpenRouter](https://openrouter.ai/) API key

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

## Usage

```bash
uv run main.py "Fix the bug in calculator/pkg/calculator.py"
```

Add `--verbose` to see token usage, tool arguments, and tool output:

```bash
uv run main.py "Run the calculator tests and fix any failures" --verbose
```

The model is currently pinned to the free `openrouter/free` route in `main.py`. Change the `model=` value there to use a specific OpenRouter model.

## Example

```bash
uv run main.py "There is a bug in the calculator. Find it, fix it, and verify the tests pass."
```

The agent inspects the free-form calculator app, edits the broken source file, runs `calculator/tests.py`, and reports what it changed.

## Project structure

```
agent47/
├── main.py                 # CLI entry point and agent loop
├── call_function.py        # Tool dispatch
├── prompts.py              # System prompt
├── config.py               # Shared configuration (max read size)
├── functions/              # Tool implementations and JSON schemas
├── calculator/             # Example project the agent operates on
├── test_*.py               # Manual tool smoke tests
└── pyproject.toml
```

## Safety notes

This agent can read, overwrite, and execute files in its working directory. Only run it on code you are willing to modify, and commit your changes before letting it loose so you can revert. Do not point it at directories containing secrets.

The tool sandbox only blocks paths outside the configured working directory. It does not sandbox the Python interpreter itself, so executed code has the same permissions as your user.

## License

MIT
