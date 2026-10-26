import os

from dotenv import load_dotenv

load_dotenv()

MAX_CHARS = 10000

# Provider configuration. Defaults to OpenRouter; override in .env for local servers.
MODEL = os.environ.get("AGENT47_MODEL", "openrouter/free")
BASE_URL = os.environ.get("AGENT47_BASE_URL", "https://openrouter.ai/api/v1")

MAX_ITERATIONS = 20

# Compaction: summarize older turns once a prompt grows past this many tokens.
CONTEXT_TOKEN_LIMIT = 16_000

# Rough token budget for the recent turns kept verbatim during compaction.
KEEP_RECENT_TOKENS = 6_000

# Caps for the generated summary and the transcript sent to the summarizer.
SUMMARY_MAX_CHARS = 6_000
TRANSCRIPT_MAX_CHARS = 60_000

# Individual tool outputs are truncated before entering the conversation.
MAX_TOOL_RESULT_CHARS = 12_000
