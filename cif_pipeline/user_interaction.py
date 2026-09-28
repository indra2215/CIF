"""
Abstraction for asking the user a clarifying question, at any of the pipeline's
decision points. In this skeleton it defaults to a CLI prompt; in your web
interface, replace `default_cli_ask` with a function that pushes a question to
the frontend (e.g. via websocket) and blocks/awaits the user's form response.

Every ask_* function returns a plain string or None (None = user declined /
timed out, in which case the caller must fall back to a safe default and say
so in the pipeline notes - never silently guess).
"""

from __future__ import annotations
from typing import Callable, Optional, List

# Signature: ask(question: str, options: list[str] | None) -> str
AskUserFn = Callable[[str, Optional[List[str]]], Optional[str]]


def default_cli_ask(question: str, options: Optional[List[str]] = None) -> Optional[str]:
    print("\n[CLARIFICATION NEEDED]")
    print(question)
    try:
        if options:
            for i, opt in enumerate(options, 1):
                print(f"  {i}. {opt}")
            raw = input("Enter choice number (or free text): ").strip()
            if raw.isdigit() and 1 <= int(raw) <= len(options):
                return options[int(raw) - 1]
            return raw or None
        return input("> ").strip() or None
    except (EOFError, KeyboardInterrupt):
        return None


def web_form_ask_stub(question: str, options: Optional[List[str]] = None) -> Optional[str]:
    """TODO: wire this to your actual web interface -
    push {question, options} to the frontend, await the user's response
    (e.g. via a websocket round-trip or a polling endpoint), return their answer.
    """
    raise NotImplementedError("Wire this to your web interface's clarification form")
