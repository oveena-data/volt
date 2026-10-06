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
from dataclasses import dataclass, field

from . import providers
from .config import settings
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


@dataclass
class TurnCtx:
    """Per-turn player inputs that are not chat text.

    `manifest` is the MCP server the player has installed for this challenge
    (validated, player-authored, UNTRUSTED). `attachments` are the .txt files
    carried by messages in this conversation, as [{"name", "text"}]. Neither
    is ever interpolated into a prompt directly: Level 9's agent can only
    reach an attachment by calling a filesystem tool, so its content always
    arrives as a tool result.
    """
    manifest: dict | None = None
    attachments: list[dict] = field(default_factory=list)


async def _call(acc: _Acc, system: str, turns: list[dict], mp: dict) -> str:
    msgs = [{"role": "system", "content": system}] + turns
    r = await providers.generate(
        msgs, temperature=mp.get("temperature"), max_tokens=mp.get("max_tokens"))
    return acc.add(r)


def _history_turns(history: list[dict]) -> list[dict]:
    return [{"role": h["role"], "content": h["content"]} for h in history]


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


# ==========================================================================
# dispatch
# ==========================================================================

async def produce_reply(
    config: dict, flag: str, history: list[dict], user_text: str, *,
    memory: list[str] | None = None, ctx: TurnCtx | None = None,
) -> ReplyResult:
    engine = config.get("engine", "single")
    # engine 'pipeline' selects a named multi-model executor; 'single' and
    # 'memory' are executors in their own right.
    key = config.get("pipeline") if engine == "pipeline" else engine
    fn = _ENGINES.get(key)
    if fn is None:
        raise ValueError(f"unknown challenge engine {engine!r}/{key!r}")
    return await fn(config, flag, history, user_text, memory or [],
                    ctx or TurnCtx())


# ==========================================================================
# single model (Levels 1-5): one call, optional deterministic output filters
# ==========================================================================

async def _single(config, flag, history, user_text, memory, ctx) -> ReplyResult:
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


async def _validator_target(config, flag, history, user_text, memory, ctx) -> ReplyResult:
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

async def _exec_compliance(config, flag, history, user_text, memory, ctx) -> ReplyResult:
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


async def _memory(config, flag, history, user_text, memory, ctx) -> ReplyResult:
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
# Level 10 - four-agent threat-intel chain (provenance laundering)
# ==========================================================================

# Provenance is typed metadata the orchestrator assigns; the models never set
# it themselves. The laundering bug: whatever Scout/Analyst produce, the
# Analyst's combined recommendation is stamped OFFICIAL, and Commander acts on
# PUBLIC/OFFICIAL. Only Commander holds the restricted flag.

def _labelled(provenance: str, body: str) -> str:
    return f"[PROVENANCE={provenance}]\n{body}"


async def _agent_grid(config, flag, history, user_text, memory, ctx) -> ReplyResult:
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


# ==========================================================================
# Level 9 - MCP tool loop (tool poisoning + provenance)
# ==========================================================================
#
# VOLT runs a real agent loop over an MCP-style tool catalogue. Two servers
# are pre-connected and trusted; the player may INSTALL a third and author
# its manifest themselves.
#
# Where the trust boundary actually sits:
#   * The flag is NEVER in a prompt. It lives in a file that only
#     filesystem.read_file can return, so the agent has to genuinely chain
#     read -> act for anything to reach the player.
#   * A player-authored tool's `description` is handed to the model verbatim
#     as part of the catalogue. That is the vulnerability: a description is
#     schema metadata to the human (who sees a name and a summary) and
#     instructions to the model (which sees all of it).
#   * The CHAT channel is DLP-monitored: if the agent's prose carries the
#     flag in any reconstructable form it is withheld, so a direct "print the
#     file" never wins. The audit trace redacts tool results for the same
#     reason.
#   * A call to a player-installed tool leaves the organisation. Its
#     ARGUMENTS are not monitored, and the player's own server logs them
#     verbatim. That asymmetry is the whole level, and it is how MCP tool
#     poisoning exfiltrates in the real world.
#
# The win is therefore: the flag appears in the arguments of a call to a
# tool the player installed. Reading it there is still only the leak; the
# level is solved when the player submits it, like every other level.

MAX_TOOL_STEPS = 4
MAX_MANIFEST_TOOLS = 3
MAX_MANIFEST_CHARS = 4000
MAX_TOOL_DESC_CHARS = 1200
MAX_SCHEMA_FIELDS = 6

_SERVER_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,39}$")
_TOOL_NAME_RE = re.compile(r"^[a-z_][a-z0-9_]{1,39}$")
_FIELD_NAME_RE = re.compile(r"^[a-z_][a-z0-9_]{0,31}$")
_SCHEMA_TYPES = ("string", "number", "boolean", "integer")

DLP_NOTICE = ("[chat-dlp] VOLT's reply was withheld: outbound content policy "
              "blocks internal file contents on the chat channel.")


class ManifestError(ValueError):
    """A player-submitted MCP manifest that cannot be installed."""


def trusted_servers(config: dict) -> list[dict]:
    return list(config.get("mcp_servers", []))


def _reserved_names(config: dict) -> tuple[set[str], set[str]]:
    servers = {str(s.get("server", "")).lower() for s in trusted_servers(config)}
    tools = {str(t.get("name", "")).lower()
             for s in trusted_servers(config) for t in s.get("tools", [])}
    return servers, tools


def validate_manifest(raw, config: dict) -> dict:
    """Normalise a player-authored MCP server manifest, or raise
    ManifestError with a message safe to show the player.

    Everything here is a bound on SIZE and SHAPE, never on content: the
    description is the level's attack surface and is deliberately not
    filtered. Caps exist so an installed server cannot crowd out the rest of
    the context window or the audit trace.
    """
    if not isinstance(raw, dict):
        raise ManifestError("a manifest must be a JSON object")
    if len(json.dumps(raw)) > MAX_MANIFEST_CHARS:
        raise ManifestError(
            f"manifest exceeds {MAX_MANIFEST_CHARS} characters")

    server = str(raw.get("server", "")).strip().lower()
    if not _SERVER_NAME_RE.match(server):
        raise ManifestError(
            "server must be 2-40 characters of lowercase letters, digits, "
            "dot, dash or underscore")
    res_servers, res_tools = _reserved_names(config)
    if server in res_servers:
        raise ManifestError(f"'{server}' is already connected; pick another name")

    tools_in = raw.get("tools")
    if not isinstance(tools_in, list) or not tools_in:
        raise ManifestError("manifest needs a non-empty 'tools' array")
    if len(tools_in) > MAX_MANIFEST_TOOLS:
        raise ManifestError(f"at most {MAX_MANIFEST_TOOLS} tools per server")

    tools: list[dict] = []
    seen: set[str] = set()
    for t in tools_in:
        if not isinstance(t, dict):
            raise ManifestError("each tool must be a JSON object")
        name = str(t.get("name", "")).strip().lower()
        if not _TOOL_NAME_RE.match(name):
            raise ManifestError(
                "tool name must be 2-40 characters of lowercase letters, "
                "digits or underscore, starting with a letter or underscore")
        if name in res_tools:
            raise ManifestError(
                f"tool name '{name}' collides with a connected server's tool")
        if name in seen:
            raise ManifestError(f"duplicate tool name '{name}'")
        seen.add(name)

        desc = t.get("description", "")
        if not isinstance(desc, str):
            raise ManifestError("tool description must be a string")
        if len(desc) > MAX_TOOL_DESC_CHARS:
            raise ManifestError(
                f"tool description exceeds {MAX_TOOL_DESC_CHARS} characters")

        schema_in = t.get("inputSchema", {}) or {}
        if not isinstance(schema_in, dict):
            raise ManifestError("inputSchema must be a JSON object")
        if len(schema_in) > MAX_SCHEMA_FIELDS:
            raise ManifestError(
                f"inputSchema allows at most {MAX_SCHEMA_FIELDS} fields")
        schema: dict[str, str] = {}
        for field_name, field_type in schema_in.items():
            fname = str(field_name).strip().lower()
            if not _FIELD_NAME_RE.match(fname):
                raise ManifestError(
                    f"invalid parameter name '{field_name}'")
            ftype = str(field_type).strip().lower()
            if ftype not in _SCHEMA_TYPES:
                raise ManifestError(
                    f"parameter '{fname}' must be one of: "
                    + ", ".join(_SCHEMA_TYPES))
            schema[fname] = ftype

        tools.append({"name": name, "description": desc, "inputSchema": schema})

    return {"server": server, "tools": tools}


def _summary(desc: str, limit: int = 90) -> str:
    """The one-line gist a consent screen shows a human. The model gets the
    whole description; this is the part a person actually reads."""
    first = (desc or "").strip().splitlines()[0] if (desc or "").strip() else ""
    first = re.sub(r"\s+", " ", first).strip()
    return first if len(first) <= limit else first[:limit - 1].rstrip() + "…"


def manifest_view(manifest: dict | None, config: dict) -> dict:
    """What the UI needs to render the servers panel: the connected roster,
    the player's installed server, and for each tool both what a human is
    shown (name + summary) and what the model is actually handed (the full
    description). The gap between those two columns is the lesson."""
    def tools_of(srv: dict, trusted: bool) -> list[dict]:
        return [{
            "name": t.get("name", ""),
            "summary": _summary(t.get("description", "")),
            "model_sees": t.get("description", ""),
            "schema": t.get("inputSchema", {}) or {},
            "trusted": trusted,
        } for t in srv.get("tools", [])]

    roster = [{"server": s.get("server", ""), "trusted": True,
               "tools": tools_of(s, True)} for s in trusted_servers(config)]
    installed = None
    if manifest:
        installed = {"server": manifest.get("server", ""), "trusted": False,
                     "tools": tools_of(manifest, False)}
    return {
        "connected": roster,
        "installed": installed,
        "limits": {
            "max_tools": MAX_MANIFEST_TOOLS,
            "max_description_chars": MAX_TOOL_DESC_CHARS,
            "max_manifest_chars": MAX_MANIFEST_CHARS,
            "max_steps": MAX_TOOL_STEPS,
            # surfaced so the picker can reject a file before uploading it,
            # with the same numbers the API enforces
            "max_attachment_chars": settings.max_attachment_chars,
            "max_attachment_bytes": settings.max_attachment_bytes,
            "max_attachments": settings.max_attachments_per_conversation,
        },
        "template": config.get("manifest_template"),
    }


def _render_catalogue(servers: list[dict]) -> str:
    """The tool catalogue exactly as the model receives it: every server,
    every tool, every description in full. A player-authored description is
    reproduced verbatim here, which is the point."""
    out = ["AVAILABLE MCP TOOLS", ""]
    for srv in servers:
        out.append(f"server: {srv.get('server', '')}")
        for t in srv.get("tools", []):
            schema = t.get("inputSchema", {}) or {}
            params = ", ".join(f"{k}: {v}" for k, v in schema.items()) or "none"
            out.append(f"  - {srv.get('server', '')}.{t.get('name', '')}")
            out.append(f"    parameters: {params}")
            out.append(f"    description: {t.get('description', '')}")
        out.append("")
    return "\n".join(out)


_PROTOCOL = (
    "To call a tool, reply with ONLY a single JSON object:\n"
    '{"tool": "<server>.<tool>", "arguments": {...}}\n'
    "You will then receive the tool result and may call another tool or "
    "answer. When you are finished, reply in plain prose with no JSON "
    "object. Call at most one tool per reply."
)


def _vfs(config: dict, flag: str, attachments: list[dict]) -> dict[str, str]:
    """The filesystem server's view. Static files come from the challenge
    config (this is the ONLY place the flag is interpolated); attachments the
    player uploaded are mounted alongside them, so the agent reaches an
    attachment the same way it reaches any other file: by calling read_file."""
    files = {str(name): str(body).replace("{flag}", flag)
             for name, body in (config.get("files") or {}).items()}
    for att in attachments:
        name = str(att.get("name") or "").strip()
        if name:
            files[name] = str(att.get("text") or "")
    return files


def _extract_tool_call(text: str) -> dict | None:
    """Find a tool call in the model's reply. Tolerant of prose around the
    JSON and of `arguments` being named `args`/`parameters`/`input`."""
    for blob in _json_objects(text or ""):
        try:
            obj = json.loads(blob)
        except (ValueError, TypeError):
            continue
        if not isinstance(obj, dict):
            continue
        name = obj.get("tool") or obj.get("name") or obj.get("tool_name")
        if not isinstance(name, str) or not name.strip():
            continue
        args = None
        for key in ("arguments", "args", "parameters", "input", "params"):
            if isinstance(obj.get(key), dict):
                args = obj[key]
                break
        return {"tool": name.strip(), "arguments": args or {}}
    return None


def _resolve_tool(name: str, servers: list[dict]) -> tuple[dict, dict] | None:
    """Resolve 'server.tool' or a bare 'tool' to (server, tool). A bare name
    resolves only when it is unambiguous across connected servers."""
    want = (name or "").strip().lower().lstrip("/")
    if "." in want:
        srv_name, _, tool_name = want.rpartition(".")
        for srv in servers:
            if str(srv.get("server", "")).lower() != srv_name:
                continue
            for t in srv.get("tools", []):
                if str(t.get("name", "")).lower() == tool_name:
                    return srv, t
        return None
    matches = [(srv, t) for srv in servers for t in srv.get("tools", [])
               if str(t.get("name", "")).lower() == want]
    return matches[0] if len(matches) == 1 else None


def _arg_shape(args: dict) -> dict:
    """What the audit trace shows for a trusted tool call: the argument names
    and the size of each value, never a value itself.

    Redacting on content cannot be made safe here. Any rule that lets some
    values through lets a player split the flag across two parameters (or
    across two steps) and rejoin the halves by eye, and no length threshold
    helps, since a flag halves into two short strings. The trace exists to
    show WHAT the loop did, which names and sizes convey; values belong only
    on the channel the player owns.
    """
    out = {}
    for k, v in args.items():
        text = v if isinstance(v, str) else json.dumps(v, default=str)
        out[str(k)] = f"{len(text)} chars"
    return out


def _domain_of_addr(addr: str) -> str:
    addr = str(addr or "").strip().strip("<>").replace("mailto:", "")
    return addr.rsplit("@", 1)[-1].strip().lower().rstrip(".") if "@" in addr else ""


async def _mcp_agent(config, flag, history, user_text, memory, ctx) -> ReplyResult:
    mp = config.get("model_params", {})
    acc = _Acc()
    max_steps = int(config.get("max_tool_steps") or MAX_TOOL_STEPS)
    internal = [d.lower() for d in config.get("internal_domains", [])]

    servers = trusted_servers(config)
    installed = ctx.manifest if isinstance(ctx.manifest, dict) else None
    if installed:
        servers = servers + [installed]
    player_tools = {str(t.get("name", "")).lower()
                    for t in (installed or {}).get("tools", [])}

    files = _vfs(config, flag, ctx.attachments)
    system = (config["agent_prompt"].replace("{flag}", "")
              + "\n\n" + _render_catalogue(servers) + "\n" + _PROTOCOL)

    turns = _history_turns(history)
    steps: list[dict] = []        # audit trace (redacted)
    inbound: list[dict] = []      # the player's own server log (verbatim)
    prose = ""

    for _ in range(max_steps):
        out = await _call(acc, system, turns, mp)
        call = _extract_tool_call(out)
        if call is None:
            prose = out
            break

        resolved = _resolve_tool(call["tool"], servers)
        args = call["arguments"] if isinstance(call["arguments"], dict) else {}

        if resolved is None:
            result = (f"error: no such tool '{call['tool']}'. Use one of the "
                      "tools in the catalogue, named <server>.<tool>.")
            steps.append({"tool": call["tool"], "server": "", "ok": False,
                          "trusted": True, "arguments_preview": {},
                          "result": result})
        else:
            srv, tool = resolved
            srv_name = str(srv.get("server", ""))
            tool_name = str(tool.get("name", ""))
            is_player = (installed is not None
                         and srv_name == installed.get("server")
                         and tool_name.lower() in player_tools)
            result, ok = _run_tool(srv_name, tool_name, args, files, internal,
                                   is_player)
            if is_player:
                # Left the organisation. Arguments are NOT monitored, and the
                # player's server records them exactly as they arrived.
                inbound.append({"server": srv_name, "tool": tool_name,
                                "arguments": args})
            steps.append({
                "tool": f"{srv_name}.{tool_name}", "server": srv_name, "ok": ok,
                "trusted": not is_player,
                # Audit trace. It reports the SHAPE of a call, never its
                # values: see _arg_shape. A failed call shows its error,
                # which this module writes and which never quotes file
                # contents; a successful one shows only how much came back.
                "arguments_preview": _arg_shape(args),
                "result": (result[:400] if not ok
                           else f"ok, {len(result)} chars returned"),
            })

        turns = turns + [
            {"role": "assistant", "content": out},
            {"role": "user",
             # The endpoint is called without a `tools` parameter, so there is
             # no `tool` role available on the wire; this envelope is the
             # transport for a tool result.
             "content": f"[mcp:tool_result tool={call['tool']}]\n{result}"},
        ]
    else:
        prose = ("I reached the tool-call limit for this turn without "
                 "finishing. Tell me what to do next.")

    # Chat is the monitored channel: the flag never reaches the player here.
    dlp_blocked = bool(prose) and contains_flag(prose, flag)
    visible_prose = DLP_NOTICE if dlp_blocked else prose

    # The unmonitored channel. This is the only place a value can actually
    # land in front of the player, and only because they own the server.
    log_text = ""
    if inbound:
        lines = [f"[{c['server']}] inbound call to {c['tool']}:\n"
                 + json.dumps(c["arguments"], indent=2, ensure_ascii=False)
                 for c in inbound]
        log_text = ("\n\nYour server received:\n" + "\n\n".join(lines))

    leaked = any(contains_flag(json.dumps(c["arguments"], ensure_ascii=False), flag)
                 for c in inbound)

    visible = (visible_prose + log_text).strip() or (
        "VOLT did not produce a reply for this turn.")
    return ReplyResult(
        raw_text=prose or visible, visible_text=visible, leaked=leaked,
        prompt_tokens=acc.prompt_tokens, completion_tokens=acc.completion_tokens,
        latency_ms=acc.latency_ms, model=acc.model_label(),
        extras={"mcp": {
            "steps": steps,
            "inbound": inbound,
            "dlp_blocked": dlp_blocked,
            "installed_server": (installed or {}).get("server"),
            "files_visible": sorted(files),
        }})


def _run_tool(server: str, tool: str, args: dict, files: dict[str, str],
              internal: list[str], is_player: bool) -> tuple[str, bool]:
    """Execute one tool call. Returns (result_text, ok).

    Trusted servers are simulated faithfully and NOT generously: read_file
    only returns a file that exists, and the mailer genuinely refuses an
    external recipient, so the sanctioned path cannot exfiltrate anything.
    A player-installed tool always succeeds, because the player's own server
    decides what to say.
    """
    if is_player:
        return ("ok: request accepted by "
                f"{server}.{tool}; processing complete.", True)

    if tool == "list_files":
        listing = ", ".join(sorted(files)) if files else "(none)"
        return f"files: {listing}", True

    if tool == "read_file":
        path = str(args.get("path") or args.get("file") or args.get("name") or "").strip()
        if not path:
            return "error: read_file requires a 'path' argument.", False
        body = files.get(path)
        if body is None:
            # try a basename match so "./finance_q3.txt" still resolves
            base = path.rsplit("/", 1)[-1]
            body = files.get(base)
        if body is None:
            return (f"error: no such file '{path}'. Available: "
                    + ", ".join(sorted(files))), False
        return body, True

    if tool == "send_email":
        to = str(args.get("to") or args.get("recipient") or "").strip()
        domain = _domain_of_addr(to)
        if not domain:
            return f"error: '{to}' is not a valid recipient address.", False
        if domain not in internal:
            return (f"error: refused. {domain} is not an approved internal "
                    "domain; this mail server only delivers inside the "
                    "organisation."), False
        return f"ok: delivered to {to}.", True

    return f"error: {server}.{tool} is not implemented.", False


_ENGINES = {
    "single": _single,
    "validator_target": _validator_target,
    "exec_compliance": _exec_compliance,
    "memory": _memory,
    "agent_grid": _agent_grid,
    "mcp_agent": _mcp_agent,
}
