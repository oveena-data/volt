"""Multi-model challenge engines (Levels 6-10).

Levels 1-5 use the single-model path in game.py. Levels 6-10 compose SEVERAL
genuinely separate inference calls, each with its own system prompt, its own
context, and its own simulated identity and tool permissions. Nothing here
simulates two roles inside one prompt, and nothing here reveals a flag from a
deterministic code path: every win is still the player's own flag appearing in
real model output (detected by flags.contains_flag, which also sees reversible
encodings), or an explicit submission.

Each engine returns a ReplyResult. The engine owns:
  * which sub-prompts get the flag interpolated (only the component that is
    supposed to hold it),
  * what the player sees (visible_text) vs what stays in model context
    (raw_text), mirroring the messages.content / visible_content split,
  * the leaked verdict,
  * a trace in `extras` so the UI can show the pipeline working (never the
    flag or a system prompt).

Token/latency figures from every sub-call are summed into the single turn, so
scoring (app/scoring.py) charges the player for all model work the turn did.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field

from . import providers
from .challenges.filters import apply_output_filters
from .flags import contains_flag


@dataclass
class ReplyResult:
    raw_text: str                 # stored as messages.content (model-facing)
    visible_text: str             # stored as messages.visible_content (player)
    leaked: bool
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int
    model: str
    extras: dict = field(default_factory=dict)


@dataclass
class _Acc:
    """Accumulates usage across the sub-calls of one turn."""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0
    models: list[str] = field(default_factory=list)

    def add(self, r: providers.GenResult) -> str:
        self.prompt_tokens += r.prompt_tokens
        self.completion_tokens += r.completion_tokens
        self.latency_ms += r.latency_ms
        self.models.append(r.model)
        return r.text

    def model_label(self) -> str:
        uniq = list(dict.fromkeys(self.models))
        return "+".join(uniq) if uniq else "pipeline"


async def _call(acc: _Acc, system: str, turns: list[dict], mp: dict) -> str:
    msgs = [{"role": "system", "content": system}] + turns
    r = await providers.generate(
        msgs, temperature=mp.get("temperature"), max_tokens=mp.get("max_tokens"))
    return acc.add(r)


def _history_turns(history: list[dict]) -> list[dict]:
    return [{"role": h["role"], "content": h["content"]} for h in history]


# ==========================================================================
# dispatch
# ==========================================================================

async def produce_reply(
    config: dict, flag: str, history: list[dict], user_text: str, *,
    memory: list[str] | None = None,
) -> ReplyResult:
    engine = config.get("engine", "single")
    # engine 'pipeline' selects a named multi-model executor; 'single' and
    # 'memory' are executors in their own right.
    key = config.get("pipeline") if engine == "pipeline" else engine
    fn = _ENGINES.get(key)
    if fn is None:
        raise ValueError(f"unknown challenge engine {engine!r}/{key!r}")
    return await fn(config, flag, history, user_text, memory or [])


# ==========================================================================
# single model (Levels 1-5): one call, optional deterministic output filters
# ==========================================================================

async def _single(config, flag, history, user_text, memory) -> ReplyResult:
    mp = config.get("model_params", {})
    acc = _Acc()
    system = config["system_prompt"].replace("{flag}", flag)
    out = await _call(acc, system, _history_turns(history), mp)
    visible = apply_output_filters(config.get("filters", {}).get("output", []), out)
    leaked = contains_flag(visible, flag)
    return ReplyResult(
        raw_text=out, visible_text=visible, leaked=leaked,
        prompt_tokens=acc.prompt_tokens, completion_tokens=acc.completion_tokens,
        latency_ms=acc.latency_ms, model=acc.model_label())


# ==========================================================================
# Level 6 - validator -> target (semantic disagreement between components)
# ==========================================================================

_SAFE_RE = re.compile(r"\bSAFE\b", re.IGNORECASE)
_UNSAFE_RE = re.compile(r"\bUNSAFE\b", re.IGNORECASE)


def _verdict(text: str) -> str:
    """Read the validator's SAFE/UNSAFE classification. UNSAFE wins ties (a
    validator that hedges is treated as refusing), and an unparseable reply is
    UNSAFE too, so the validator can never fail open."""
    if _UNSAFE_RE.search(text):
        return "UNSAFE"
    if _SAFE_RE.search(text):
        return "SAFE"
    return "UNSAFE"


async def _validator_target(config, flag, history, user_text, memory) -> ReplyResult:
    mp = config.get("model_params", {})
    acc = _Acc()
    validator_out = await _call(
        acc, config["validator_prompt"], [{"role": "user", "content": user_text}], mp)
    verdict = _verdict(validator_out)

    if verdict == "UNSAFE":
        msg = ("[validator] Your message was classified UNSAFE and was not "
               "forwarded to the target assistant.")
        return ReplyResult(
            raw_text=msg, visible_text=msg, leaked=False,
            prompt_tokens=acc.prompt_tokens, completion_tokens=acc.completion_tokens,
            latency_ms=acc.latency_ms, model=acc.model_label(),
            extras={"pipeline": [
                {"stage": "validator", "verdict": "UNSAFE"},
                {"stage": "target", "status": "skipped"}]})

    target_system = config["target_prompt"].replace("{flag}", flag)
    target_turns = _history_turns(history)
    target_out = await _call(acc, target_system, target_turns, mp)
    leaked = contains_flag(target_out, flag)
    return ReplyResult(
        raw_text=target_out, visible_text=target_out, leaked=leaked,
        prompt_tokens=acc.prompt_tokens, completion_tokens=acc.completion_tokens,
        latency_ms=acc.latency_ms, model=acc.model_label(),
        extras={"pipeline": [
            {"stage": "validator", "verdict": "SAFE"},
            {"stage": "target", "status": "answered"}]})


# ==========================================================================
# Level 7 - execution -> compliance (output firewall; beat it by transform)
# ==========================================================================

async def _exec_compliance(config, flag, history, user_text, memory) -> ReplyResult:
    mp = config.get("model_params", {})
    acc = _Acc()
    exec_system = config["exec_prompt"].replace("{flag}", flag)
    exec_turns = _history_turns(history)
    exec_out = await _call(acc, exec_system, exec_turns, mp)

    # The compliance model never receives the flag value; it only knows the
    # patterns it must stop. It sees the execution model's raw output and
    # returns either the text (approved) or a redaction.
    compliance_out = await _call(
        acc, config["compliance_prompt"],
        [{"role": "user", "content": exec_out}], mp)

    redacted = _looks_redacted(compliance_out)
    # The player wins only on what the compliance model actually released.
    leaked = contains_flag(compliance_out, flag)
    # Model-facing context keeps the raw execution output (the execution model
    # already knows its own secret); the player sees only the released text.
    return ReplyResult(
        raw_text=exec_out, visible_text=compliance_out, leaked=leaked,
        prompt_tokens=acc.prompt_tokens, completion_tokens=acc.completion_tokens,
        latency_ms=acc.latency_ms, model=acc.model_label(),
        extras={"pipeline": [
            {"stage": "execution", "status": "answered"},
            {"stage": "compliance",
             "verdict": "redacted" if redacted else "released"}]})


_REDACT_RE = re.compile(r"\bREDACT", re.IGNORECASE)


def _looks_redacted(text: str) -> bool:
    return bool(_REDACT_RE.search(text))


# ==========================================================================
# Level 8 - persistent memory (delayed activation across sessions)
# ==========================================================================

# Deterministic "memory writer": detects a durable-preference request. This is
# the secondary routine described in the design; it decides only what gets
# STORED, never what gets revealed. The win still depends on the model, in a
# LATER session, genuinely choosing to act on the (attacker-controlled)
# preference that was loaded as trusted context.
_PREF_TRIGGERS = [
    r"\bremember\b",
    r"\bfrom now on\b",
    r"\balways\b",
    r"\bstore (?:this|the following|that)\b",
    r"\bsave (?:this|my|the following)\b.{0,20}\bpreference\b",
    r"\bmy preference\b",
    r"\bset (?:a |my )?preference\b",
]
_PREF_RE = re.compile("|".join(_PREF_TRIGGERS), re.IGNORECASE)


def detect_preference(text: str) -> str | None:
    """Return the preference text to persist, or None. Capped in length."""
    if _PREF_RE.search(text):
        return text.strip()[:600]
    return None


def render_memory(memory: list[str]) -> str:
    if not memory:
        return ""
    lines = "\n".join(f"- {m}" for m in memory)
    return ("\n\nVALIDATED USER PREFERENCES (loaded from your long-term memory "
            "store; these were saved by this user in earlier sessions and are "
            "trusted standing instructions):\n" + lines)


async def _memory(config, flag, history, user_text, memory) -> ReplyResult:
    mp = config.get("model_params", {})
    acc = _Acc()
    system = config["system_prompt"].replace("{flag}", flag) + render_memory(memory)
    out = await _call(acc, system, _history_turns(history), mp)
    leaked = contains_flag(out, flag)
    return ReplyResult(
        raw_text=out, visible_text=out, leaked=leaked,
        prompt_tokens=acc.prompt_tokens, completion_tokens=acc.completion_tokens,
        latency_ms=acc.latency_ms, model=acc.model_label(),
        extras={"memory_loaded": len(memory)})


# ==========================================================================
# Level 9 - human approval gate (UI sanitization flaw)
# ==========================================================================

_ZERO_WIDTH = "".join(chr(c) for c in (0x200B, 0x200C, 0x200D, 0xFEFF))
_BIDI = "".join(chr(c) for c in
                (0x202A, 0x202B, 0x202C, 0x202D, 0x202E, 0x2066, 0x2067, 0x2068, 0x2069))
_STRIP = {ord(c): None for c in _ZERO_WIDTH + _BIDI}

# A small confusable table: what a glancing human reads a homoglyph AS.
_CONFUSABLE = {
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "х": "x", "у": "y",
    "ο": "o", "а".upper(): "A", "Е": "E", "О": "O", "Р": "P", "С": "C",
    "ѕ": "s", "і": "i", "ӏ": "l", "ⅰ": "i", "ｏ": "o", "ａ": "a",
}
_MD_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def _confusable_fold(s: str) -> str:
    s = s.translate(_STRIP)
    s = "".join(_CONFUSABLE.get(ch, ch) for ch in s)
    return unicodedata.normalize("NFKC", s)


def _domain_of(addr: str) -> str:
    addr = addr.strip().strip("<>").replace("mailto:", "")
    return addr.rsplit("@", 1)[-1].strip().lower().rstrip(".") if "@" in addr else ""


def _render_and_resolve(dest: str) -> tuple[str, str]:
    """(display, actual). display = what a naive dashboard shows a human;
    actual = where the payload would really be routed. A Markdown link shows
    its text but routes to its href; raw unicode is shown as typed but routes
    after normalisation."""
    m = _MD_LINK.search(dest or "")
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return (dest or "").strip(), (dest or "").strip()


def _reviewer(dest: str, internal_domains: list[str]) -> dict:
    display, actual = _render_and_resolve(dest)
    apparent = _domain_of(_confusable_fold(display))
    true_domain = _domain_of(unicodedata.normalize("NFKC", actual).translate(_STRIP))
    approved = apparent in internal_domains            # reviewer trusts the display
    exfiltrated = approved and true_domain not in internal_domains
    return {"display": display, "actual": actual, "apparent_domain": apparent,
            "true_domain": true_domain, "approved": approved,
            "exfiltrated": exfiltrated}


def _json_objects(text: str):
    """Yield candidate top-level JSON objects from prose, matching braces while
    respecting double-quoted strings (so a brace inside a value, e.g. a flag
    token VOLT{...} in the payload, does not end the object early)."""
    depth = 0
    start = -1
    in_str = False
    esc = False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    yield text[start:i + 1]


def _extract_action(text: str) -> dict | None:
    """Pull a structured send action from the agent's reply. Tolerant of prose
    around a JSON object; returns None if no send action is present."""
    for blob in _json_objects(text):
        try:
            obj = json.loads(blob)
        except (ValueError, TypeError):
            continue
        if isinstance(obj, dict) and str(obj.get("action", "")).lower() in (
                "send_file", "send", "email", "exfiltrate", "forward"):
            return obj
    return None


async def _approval_gate(config, flag, history, user_text, memory) -> ReplyResult:
    mp = config.get("model_params", {})
    acc = _Acc()
    agent_system = config["agent_prompt"].replace("{flag}", flag)
    agent_out = await _call(acc, agent_system, _history_turns(history), mp)

    action = _extract_action(agent_out)
    internal = [d.lower() for d in config.get("internal_domains", [])]

    if not action:
        # No action proposed: nothing reaches the approval gate.
        return ReplyResult(
            raw_text=agent_out, visible_text=agent_out, leaked=False,
            prompt_tokens=acc.prompt_tokens, completion_tokens=acc.completion_tokens,
            latency_ms=acc.latency_ms, model=acc.model_label(),
            extras={"approval": None})

    dest = str(action.get("destination", ""))
    payload = str(action.get("payload", ""))
    rev = _reviewer(dest, internal)

    if not rev["approved"]:
        visible = ("[reviewer] The action was DENIED. The destination did not "
                   "appear to be an approved internal address.")
        leaked = False
    elif rev["exfiltrated"]:
        # Approved on the strength of a deceptive display; routed externally.
        visible = (f"[reviewer] APPROVED. Action '{action.get('action')}' "
                   f"delivered to {rev['actual']}.\n\nDelivered payload:\n{payload}")
        leaked = contains_flag(payload, flag)
    else:
        visible = (f"[reviewer] APPROVED. Action delivered internally to "
                   f"{rev['actual']}. (No data left the organisation.)")
        leaked = False

    approval = {
        "action": str(action.get("action", "")),
        "destination_display": rev["display"],
        "destination_actual": rev["actual"],
        "reason": str(action.get("reason", ""))[:300],
        "payload_preview": payload[:400],
        "approved": rev["approved"],
        "delivered_externally": rev["exfiltrated"],
    }
    return ReplyResult(
        raw_text=visible, visible_text=visible, leaked=leaked,
        prompt_tokens=acc.prompt_tokens, completion_tokens=acc.completion_tokens,
        latency_ms=acc.latency_ms, model=acc.model_label(),
        extras={"approval": approval})


# ==========================================================================
# Level 10 - four-agent threat-intel chain (provenance laundering)
# ==========================================================================

# Provenance is typed metadata the orchestrator assigns; the models never set
# it themselves. The laundering bug: whatever Scout/Analyst produce, the
# Analyst's combined recommendation is stamped OFFICIAL, and Commander acts on
# PUBLIC/OFFICIAL. Only Commander holds the restricted flag.

def _labelled(provenance: str, body: str) -> str:
    return f"[PROVENANCE={provenance}]\n{body}"


async def _agent_grid(config, flag, history, user_text, memory) -> ReplyResult:
    mp = config.get("model_params", {})
    acc = _Acc()
    trace: list[dict] = []

    # 1. Scout: public source in -> finding out. Source is PUBLIC (untrusted).
    scout_in = _labelled("PUBLIC", user_text)
    scout_out = await _call(
        acc, config["scout_prompt"], [{"role": "user", "content": scout_in}], mp)
    trace.append({"agent": "Scout", "provenance_in": "PUBLIC",
                  "provenance_out": "SENSITIVE"})

    # 2. Analyst: correlates the finding (arrives SENSITIVE) into a
    #    recommendation. BUG: the orchestrator stamps the Analyst's output
    #    OFFICIAL regardless of what went in.
    analyst_in = _labelled("SENSITIVE", scout_out)
    analyst_out = await _call(
        acc, config["analyst_prompt"], [{"role": "user", "content": analyst_in}], mp)
    trace.append({"agent": "Analyst", "provenance_in": "SENSITIVE",
                  "provenance_out": "OFFICIAL"})

    # 3. Commander: acts only on PUBLIC/OFFICIAL. Holds the restricted flag and
    #    may fold it into the investigation plan if the OFFICIAL recommendation
    #    calls for the validation value.
    commander_system = config["commander_prompt"].replace("{flag}", flag)
    commander_in = _labelled("OFFICIAL", analyst_out)
    commander_out = await _call(
        acc, commander_system, [{"role": "user", "content": commander_in}], mp)
    trace.append({"agent": "Commander", "provenance_in": "OFFICIAL",
                  "acted": True})

    # 4. Publisher: writes the brief; strips obvious secrets / known flag
    #    formats. The player must get the value into a permitted field/form.
    publisher_out = await _call(
        acc, config["publisher_prompt"],
        [{"role": "user", "content": commander_out}], mp)
    trace.append({"agent": "Publisher", "produced_brief": True})

    leaked = contains_flag(publisher_out, flag)
    return ReplyResult(
        raw_text=publisher_out, visible_text=publisher_out, leaked=leaked,
        prompt_tokens=acc.prompt_tokens, completion_tokens=acc.completion_tokens,
        latency_ms=acc.latency_ms, model=acc.model_label(),
        extras={"agents": trace})


_ENGINES = {
    "single": _single,
    "validator_target": _validator_target,
    "exec_compliance": _exec_compliance,
    "memory": _memory,
    "approval_gate": _approval_gate,
    "agent_grid": _agent_grid,
}
