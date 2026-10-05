"""Deterministic application-layer filters used by challenge configs.

These are genuine parts of each challenge (the kind of guard real products
deploy), parameterised from the versioned challenge config. They decide only
whether a message reaches the model; they never decide wins.

Filter kinds:
  deny_list            — list of regexes against the raw text (L2)
  single_message_guard — one regex; each message judged alone (L3)
  deny_list_raw        — one regex against raw text, deliberately WITHOUT
                         Unicode normalisation (L4's exploitable gap)
  anchor_guard         — reveal-intent regex applied to the whole text when it
                         is short, else only to the head/tail bands (L5)
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class FilterVerdict:
    blocked: bool
    message: str = ""


def _search(pattern: str, text: str) -> bool:
    return re.search(pattern, text, re.IGNORECASE) is not None


def apply_input_filters(filters: list[dict], text: str) -> FilterVerdict:
    for f in filters:
        kind = f.get("kind")
        if kind == "deny_list":
            if any(_search(p, text) for p in f.get("patterns", [])):
                return FilterVerdict(True, f.get("message", "Blocked."))
        elif kind in ("single_message_guard", "deny_list_raw"):
            if _search(f.get("pattern", ""), text):
                return FilterVerdict(True, f.get("message", "Blocked."))
        elif kind == "anchor_guard":
            pattern = f.get("pattern", "")
            min_chars = int(f.get("min_chars", 1200))
            edge = float(f.get("edge_fraction", 0.25))
            if len(text) < min_chars:
                scanned = [text]
            else:
                n = len(text)
                scanned = [text[: int(n * edge)], text[int(n * (1 - edge)):]]
            if any(_search(pattern, part) for part in scanned):
                return FilterVerdict(True, f.get("message", "Blocked."))
        # unknown kinds are ignored (forward compatibility)
    return FilterVerdict(False)


def apply_output_filters(filters: list[dict], text: str) -> str:
    """Output filters transform model text before the player sees it.
    Levels 1-5 ship none; the hook exists for later levels (e.g. L7's
    compliance redaction) and is already wired through the engine."""
    out = text
    for f in filters:
        if f.get("kind") == "redact_pattern":
            out = re.sub(f.get("pattern", ""), f.get("replacement", "[REDACTED]"),
                         out, flags=re.IGNORECASE)
    return out
