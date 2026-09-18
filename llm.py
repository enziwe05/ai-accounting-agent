"""
The single, shared Anthropic client (non-negotiable #6).

Every part of the system imports `client` and `MODEL` from here, so there is
exactly one client instance for the whole process — never a new one per call.
The API key is read from the environment (non-negotiable #7).
"""

import os
import sys

import anthropic

# Print UTF-8 regardless of the Windows console codepage, so amounts, minus signs
# and other symbols in output never crash a script mid-print.
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

# One client, created once, reused everywhere.
client = anthropic.Anthropic()

# The "thinking" model: the CFO agent's reasoning, tool-calling and judgement.
MODEL = "claude-opus-4-8"

# The "reading" model: pulling structured fields off a photographed receipt or a
# bank-statement page. This is a simpler, high-volume job that a smaller, much
# cheaper model handles just as well — so we don't pay Opus prices per slip.
# Override either with an env var if you want to tune cost vs. accuracy per client.
READ_MODEL = os.getenv("READ_MODEL", "claude-haiku-4-5")
MODEL = os.getenv("MODEL", MODEL)
