"""
The single, shared Anthropic client (non-negotiable #6).

Every part of the system imports `client` and `MODEL` from here, so there is
exactly one client instance for the whole process — never a new one per call.
The API key is read from the environment (non-negotiable #7).
"""

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

MODEL = "claude-opus-4-8"
