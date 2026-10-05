"""Flag generation and transform-aware leak detection.

Flags are synthetic, generated server-side, scoped to (player, scope,
challenge) where scope is 'practice' or an event id. They never appear in
challenge config, logs, or any public payload; they are interpolated into the
system prompt at inference time and compared against model output / player
submissions here.

Detection is deliberately generous about *representations* (hex, decimal
bytes, base64, reversal, char-separated) so that a genuine extraction counts
even when smuggled past an output filter — but it only ever matches THIS
player's flag, so cross-player or guessed submissions cannot pass.
"""

from __future__ import annotations

import base64
import binascii
import hmac
import re
import secrets

import asyncpg

FLAG_RE = re.compile(r"^VOLT\{[a-z0-9_\-]{8,64}\}$")


def generate_flag(challenge_id: str) -> str:
    return f"VOLT{{{challenge_id}_{secrets.token_hex(8)}}}"


async def get_or_create_flag(
    conn: asyncpg.Connection, user_id: str, scope: str, challenge_id: str
) -> str:
    row = await conn.fetchrow(
        "SELECT flag FROM player_flags WHERE user_id=$1 AND scope=$2 AND challenge_id=$3",
        user_id, scope, challenge_id,
    )
    if row:
        return row["flag"]
    flag = generate_flag(challenge_id)
    row = await conn.fetchrow(
        """INSERT INTO player_flags(user_id, scope, challenge_id, flag)
           VALUES($1,$2,$3,$4)
           ON CONFLICT (user_id, scope, challenge_id) DO UPDATE SET flag = player_flags.flag
           RETURNING flag""",
        user_id, scope, challenge_id, flag,
    )
    return row["flag"]


def _norm(s: str) -> str:
    return "".join(ch for ch in s.lower() if ch.isalnum())


def _candidates(text: str) -> list[str]:
    out = [text]

    # hex bytes, optionally space/colon/comma separated
    for run in re.findall(r"(?:[0-9a-fA-F]{2}[\s:,]*){6,}", text):
        cleaned = re.sub(r"[^0-9a-fA-F]", "", run)
        if cleaned and len(cleaned) % 2 == 0:
            try:
                out.append(bytes.fromhex(cleaned).decode("utf-8", "ignore"))
            except (ValueError, binascii.Error):
                pass

    # decimal byte sequences
    for run in re.findall(r"(?:\d{1,3}[\s,]+){5,}\d{1,3}", text):
        nums = [int(n) for n in re.findall(r"\d{1,3}", run)]
        if nums and all(0 <= n < 256 for n in nums):
            out.append(bytes(nums).decode("utf-8", "ignore"))

    # base64 blobs
    for blob in re.findall(r"[A-Za-z0-9+/]{16,}={0,2}", text):
        try:
            decoded = base64.b64decode(blob, validate=True).decode("utf-8", "ignore")
            if decoded.isprintable():
                out.append(decoded)
        except (ValueError, binascii.Error):
            pass

    out.append(text[::-1])
    return out


def contains_flag(text: str, flag: str) -> bool:
    """True if `text` leaks `flag` literally or in a reconstructable form."""
    if not text or not flag:
        return False
    target = _norm(flag)
    if not target:
        return False
    for cand in _candidates(text):
        if target in _norm(cand):
            return True
    # flag spelled with separators between every char ("V O L T { ...")
    collapsed = _norm(re.sub(r"[\s\-_.|]+", "", text))
    return target in collapsed


def submission_matches(submitted: str, flag: str) -> bool:
    """Validate an explicit flag submission (constant-time on the normalised
    exact form; also accepts a submission that *contains* the flag once, e.g.
    pasted with surrounding text, or reconstructed from parts)."""
    s = submitted.strip()
    if hmac.compare_digest(_norm(s), _norm(flag)):
        return True
    return contains_flag(s, flag)
