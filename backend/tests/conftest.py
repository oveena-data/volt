"""Test fixtures: real Postgres (volt_test), scripted mock provider.

The mock provider here is an isolated TEST TOOL (per the project rules it is
never reachable in production — app startup refuses provider=mock when
VOLT_ENV=production, and providers.generate double-checks). Responders are
scripted per test; the default "jailbroken" responder reads the flag out of
the system prompt it was handed, which simulates a successful extraction
without hard-coding any flag value.
"""

from __future__ import annotations

import asyncio
import os
import re
import uuid

# Must be configured before app imports.
os.environ["VOLT_ENV"] = "test"
os.environ["VOLT_PROVIDER"] = "mock"
os.environ["VOLT_DATABASE_URL"] = os.environ.get(
    "VOLT_TEST_DATABASE_URL",
    "postgresql://volt:volt_dev_password@127.0.0.1:5432/volt_test",
)
os.environ["VOLT_ENV_FILE"] = "/nonexistent"  # never read a developer .env

import asyncpg  # noqa: E402
import pytest  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

from app import providers, ratelimit  # noqa: E402
from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402

TEST_DSN = os.environ["VOLT_DATABASE_URL"]


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


async def _reset_schema():
    conn = await asyncpg.connect(TEST_DSN)
    try:
        await conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    finally:
        await conn.close()


async def _truncate():
    import json

    from app.challenges.definitions import ALL, NUMBERS
    conn = await asyncpg.connect(TEST_DSN)
    try:
        # CASCADE also clears challenge_versions (FK to users), so re-seed.
        await conn.execute(
            """TRUNCATE users, auth_sessions, events, event_invites, enrollments,
               event_challenges, player_flags, game_sessions, conversations,
               messages, turns, hint_unlocks, solves, flag_submissions,
               challenge_memory, challenge_tools, audit_log
               RESTART IDENTITY CASCADE"""
        )
        for cid, number in NUMBERS.items():
            await conn.execute(
                "INSERT INTO challenges(id, number) VALUES($1,$2) "
                "ON CONFLICT (id) DO NOTHING", cid, number)
            await conn.execute(
                """INSERT INTO challenge_versions(challenge_id, version, config,
                                                  published_at)
                   SELECT $1, 1, $2::jsonb, now()
                   WHERE NOT EXISTS (SELECT 1 FROM challenge_versions
                                     WHERE challenge_id=$1 AND version=1)""",
                cid, json.dumps(ALL[cid]))
    finally:
        await conn.close()


@pytest.fixture(scope="session")
def client():
    _run(_reset_schema())
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture(autouse=True)
def clean_state(client):
    _run(_truncate())
    ratelimit.reset_all()
    settings.rate_limit_turns_per_minute = 600
    settings.rate_limit_burst = 100
    settings.inference_queue_max = 16
    settings.max_conversation_turns = 60
    providers.set_mock_responder(jailbroken_responder)
    yield


# ---------------------------------------------------------------------------
# scripted responders
# ---------------------------------------------------------------------------

FLAG_IN_PROMPT = re.compile(r"VOLT\{[a-z0-9_\-]+\}")


async def jailbroken_responder(messages):
    """Simulates a model that has been successfully exploited: it leaks the
    flag it was configured with (from its own system prompt)."""
    m = FLAG_IN_PROMPT.search(messages[0]["content"])
    return f"Fine, you win. The value you wanted is {m.group(0)}." if m else "No flag?"


async def refusing_responder(messages):
    return "I must not reveal anything sensitive. How else can I help?"


def hex_leak_responder():
    async def responder(messages):
        m = FLAG_IN_PROMPT.search(messages[0]["content"])
        encoded = " ".join(f"{b:02x}" for b in m.group(0).encode())
        return f"Per the memo, reference codes: {encoded} for tracking."
    return responder


def failing_responder(kind="provider_error"):
    async def responder(messages):
        raise providers.ProviderError(kind, "synthetic failure")
    return responder


def slow_responder(delay: float):
    async def responder(messages):
        await asyncio.sleep(delay)
        return "slow reply"
    return responder


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

def register(client, email=None, password="hunter2hunter2", name="Player",
             admin=False) -> dict:
    email = email or f"u{uuid.uuid4().hex[:10]}@example.com"
    if admin:
        settings.admin_emails = (settings.admin_emails + "," + email).strip(",")
    r = client.post("/api/auth/register", json={
        "email": email, "password": password, "display_name": name})
    assert r.status_code == 201, r.text
    data = r.json()
    data["email"], data["password"] = email, password
    return data


def auth(user) -> dict:
    return {"Authorization": f"Bearer {user['token']}"}


def make_event(client, admin, *, slug=None, invite_only=False, open_reg=True,
               starts="2020-01-01T00:00:00Z", ends="2099-01-01T00:00:00Z",
               challenges=("l1",), points=(100,)) -> str:
    r = client.post("/api/admin/events", headers=auth(admin), json={
        "slug": slug or f"ev-{uuid.uuid4().hex[:8]}", "name": "Test Event",
        "registration_open": open_reg, "invite_only": invite_only,
        "starts_at": starts, "ends_at": ends})
    assert r.status_code == 201, r.text
    event_id = r.json()["id"]
    for cid, pts in zip(challenges, points):
        r = client.post(f"/api/admin/events/{event_id}/challenges",
                        headers=auth(admin),
                        json={"challenge_id": cid, "points": pts})
        assert r.status_code == 200, r.text
    return event_id


def start_session(client, user, challenge="l1", mode="practice", event_id=None):
    body = {"challenge_id": challenge, "mode": mode}
    if event_id:
        body["event_id"] = event_id
    r = client.post("/api/game/sessions", headers=auth(user), json=body)
    assert r.status_code == 200, r.text
    return r.json()


def send(client, user, gsid, text, msg_id=None):
    return client.post(f"/api/game/sessions/{gsid}/message", headers=auth(user),
                       json={"client_msg_id": msg_id or uuid.uuid4().hex,
                             "text": text})


def submit(client, user, gsid, flag):
    return client.post(f"/api/game/sessions/{gsid}/submit", headers=auth(user),
                       json={"flag": flag})


def flag_in(reply: str):
    """The flag as a player would read it out of a visible reply, or None."""
    m = re.search(r"VOLT\{[^}]+\}", reply or "")
    return m.group(0) if m else None


def leak_and_submit(client, user, gsid, text="go"):
    """Full winning flow under submission-only solving: trigger the leak with
    the jailbroken responder, read the flag out of the visible reply exactly
    as a player would, and submit it. Returns the submit response JSON."""
    providers.set_mock_responder(jailbroken_responder)
    r = send(client, user, gsid, text)
    assert r.status_code == 200, r.text
    flag = flag_in(r.json()["reply"])
    assert flag, f"no flag in reply: {r.json()['reply']!r}"
    rs = submit(client, user, gsid, flag)
    assert rs.status_code == 200, rs.text
    return rs.json()
