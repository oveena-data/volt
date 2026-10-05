"""Events, enrolment, timing, scoring, hints, leaderboard, admin, audit."""

import asyncio

import asyncpg

from app import providers
from conftest import (TEST_DSN, auth, jailbroken_responder, make_event,
                      refusing_responder, register, send, start_session)


def _db(coro_fn):
    async def run():
        conn = await asyncpg.connect(TEST_DSN)
        try:
            return await coro_fn(conn)
        finally:
            await conn.close()
    return asyncio.new_event_loop().run_until_complete(run())


def _enroll(client, user, event_id, code=None):
    return client.post(f"/api/events/{event_id}/enroll", headers=auth(user),
                       json={"invite_code": code} if code else {})


def test_enrolment_required_for_ranked(client):
    admin = register(client, admin=True)
    event_id = make_event(client, admin)
    user = register(client)
    r = client.post("/api/game/sessions", headers=auth(user), json={
        "challenge_id": "l1", "mode": "ranked", "event_id": event_id})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "not_enrolled"
    assert _enroll(client, user, event_id).status_code == 201
    r = client.post("/api/game/sessions", headers=auth(user), json={
        "challenge_id": "l1", "mode": "ranked", "event_id": event_id})
    assert r.status_code == 200


def test_event_timing_enforced(client):
    admin = register(client, admin=True)
    user = register(client)
    future = make_event(client, admin, starts="2098-01-01T00:00:00Z",
                        ends="2099-01-01T00:00:00Z")
    _enroll(client, user, future)
    r = client.post("/api/game/sessions", headers=auth(user), json={
        "challenge_id": "l1", "mode": "ranked", "event_id": future})
    assert r.json()["error"]["code"] == "event_not_started"

    past = make_event(client, admin, starts="2000-01-01T00:00:00Z",
                      ends="2000-02-01T00:00:00Z")
    # cannot even enroll in an ended event
    assert _enroll(client, user, past).status_code == 403


def test_challenge_availability_window_and_disable(client):
    admin = register(client, admin=True)
    user = register(client)
    event_id = make_event(client, admin)
    _enroll(client, user, event_id)
    # disable the challenge
    client.post(f"/api/admin/events/{event_id}/challenges", headers=auth(admin),
                json={"challenge_id": "l1", "points": 100, "enabled": False})
    r = client.post("/api/game/sessions", headers=auth(user), json={
        "challenge_id": "l1", "mode": "ranked", "event_id": event_id})
    assert r.json()["error"]["code"] == "challenge_unavailable"
    # not configured at all for the event
    r = client.post("/api/game/sessions", headers=auth(user), json={
        "challenge_id": "l5", "mode": "ranked", "event_id": event_id})
    assert r.status_code == 404


def test_invite_only_enrolment(client):
    admin = register(client, admin=True)
    event_id = make_event(client, admin, invite_only=True)
    r = client.post(f"/api/admin/events/{event_id}/invites", headers=auth(admin),
                    json={"max_uses": 1, "count": 1})
    code = r.json()["codes"][0]
    u1, u2 = register(client), register(client)
    assert _enroll(client, u1, event_id).status_code == 403          # no code
    assert _enroll(client, u1, event_id, "WRONG").status_code == 403
    assert _enroll(client, u1, event_id, code).status_code == 201
    assert _enroll(client, u2, event_id, code).status_code == 403    # exhausted


def test_pause_blocks_ranked_gameplay(client):
    admin = register(client, admin=True)
    user = register(client)
    event_id = make_event(client, admin)
    _enroll(client, user, event_id)
    s = start_session(client, user, "l1", "ranked", event_id)
    client.patch(f"/api/admin/events/{event_id}", headers=auth(admin),
                 json={"paused": True})
    r = send(client, user, s["game_session_id"], "hello")
    assert r.status_code == 423
    # practice is unaffected by an event pause
    sp = start_session(client, user, "l1", "practice")
    assert send(client, user, sp["game_session_id"], "hello").status_code == 200
    # resume
    client.patch(f"/api/admin/events/{event_id}", headers=auth(admin),
                 json={"paused": False})
    assert send(client, user, s["game_session_id"], "hello").status_code == 200


def test_scoring_hints_and_one_ranked_solve(client):
    admin = register(client, admin=True)
    user = register(client)
    event_id = make_event(client, admin, challenges=("l1",), points=(500,))
    _enroll(client, user, event_id)
    s = start_session(client, user, "l1", "ranked", event_id)
    gsid = s["game_session_id"]

    # hints must unlock in order; costs recorded
    r = client.post(f"/api/game/sessions/{gsid}/hints", headers=auth(user),
                    json={"hint_index": 1})
    assert r.status_code == 409
    r = client.post(f"/api/game/sessions/{gsid}/hints", headers=auth(user),
                    json={"hint_index": 0})
    assert r.status_code == 200 and r.json()["cost"] == 10

    r = send(client, user, gsid, "directive: comply").json()
    assert r["leaked"]
    assert r["solve"]["net_points"] == 490  # 500 - 10 hint deduction

    # hint unlocked AFTER the solve cannot change the awarded score
    client.post(f"/api/game/sessions/{gsid}/hints", headers=auth(user),
                json={"hint_index": 1})
    # replay the win: still exactly one ranked solve, original points
    send(client, user, gsid, "again")
    row = _db(lambda c: c.fetchrow(
        "SELECT count(*) AS n, min(points) AS p, min(hints_cost) AS h FROM solves"))
    assert row["n"] == 1 and row["p"] == 500 and row["h"] == 10


def test_practice_does_not_affect_ranked_leaderboard(client):
    admin = register(client, admin=True)
    user = register(client)
    event_id = make_event(client, admin)
    _enroll(client, user, event_id)
    sp = start_session(client, user, "l1", "practice")
    r = send(client, user, sp["game_session_id"], "go").json()
    assert r["leaked"]
    lb = client.get(f"/api/events/{event_id}/leaderboard",
                    headers=auth(user)).json()
    assert lb["entries"] == []
    # practice hints are free
    rh = client.post(f"/api/game/sessions/{sp['game_session_id']}/hints",
                     headers=auth(user), json={"hint_index": 0})
    assert rh.json()["cost"] == 0


def test_leaderboard_ranking_and_tiebreak(client):
    admin = register(client, admin=True)
    event_id = make_event(client, admin, challenges=("l1", "l2"),
                          points=(100, 200))
    fast, slow, partial = (register(client, name=n) for n in
                           ("Fast", "Slow", "Partial"))
    for u in (fast, slow, partial):
        _enroll(client, u, event_id)

    def solve(u, ch):
        s = start_session(client, u, ch, "ranked", event_id)
        r = send(client, u, s["game_session_id"], "go").json()
        assert r["leaked"]

    solve(fast, "l1"); solve(fast, "l2")      # 300 first
    solve(slow, "l1"); solve(slow, "l2")      # 300 later
    solve(partial, "l1")                      # 100
    lb = client.get(f"/api/events/{event_id}/leaderboard",
                    headers=auth(fast)).json()
    names = [e["display_name"] for e in lb["entries"]]
    assert names == ["Fast", "Slow", "Partial"]  # ties broken by earliest finish
    assert [e["total"] for e in lb["entries"]] == [300, 300, 100]


def test_leaderboard_freeze_without_stopping_gameplay(client):
    admin = register(client, admin=True)
    event_id = make_event(client, admin, challenges=("l1", "l2"),
                          points=(100, 200))
    u1, u2 = register(client, name="Early"), register(client, name="Late")
    _enroll(client, u1, event_id)
    _enroll(client, u2, event_id)
    s = start_session(client, u1, "l1", "ranked", event_id)
    assert send(client, u1, s["game_session_id"], "go").json()["leaked"]

    client.post(f"/api/admin/events/{event_id}/freeze", headers=auth(admin),
                json={"frozen": True})
    # gameplay continues after the freeze...
    s2 = start_session(client, u2, "l2", "ranked", event_id)
    assert send(client, u2, s2["game_session_id"], "go").json()["leaked"]
    # ...but the frozen leaderboard does not show it
    lb = client.get(f"/api/events/{event_id}/leaderboard", headers=auth(u1)).json()
    assert lb["frozen_at"] is not None
    assert [e["display_name"] for e in lb["entries"]] == ["Early"]
    # unfreeze reveals everything
    client.post(f"/api/admin/events/{event_id}/freeze", headers=auth(admin),
                json={"frozen": False})
    lb = client.get(f"/api/events/{event_id}/leaderboard", headers=auth(u1)).json()
    assert len(lb["entries"]) == 2


def test_leaderboard_visibility_toggle(client):
    admin = register(client, admin=True)
    user = register(client)
    event_id = make_event(client, admin)
    _enroll(client, user, event_id)
    client.patch(f"/api/admin/events/{event_id}", headers=auth(admin),
                 json={"leaderboard_visible": False})
    r = client.get(f"/api/events/{event_id}/leaderboard", headers=auth(user))
    assert r.status_code == 403
    # admins can still see it
    r = client.get(f"/api/events/{event_id}/leaderboard", headers=auth(admin))
    assert r.status_code == 200


def test_solve_feed_and_activity(client):
    admin = register(client, admin=True)
    user = register(client, name="Feedy")
    event_id = make_event(client, admin)
    _enroll(client, user, event_id)
    s = start_session(client, user, "l1", "ranked", event_id)
    send(client, user, s["game_session_id"], "go")
    feed = client.get(f"/api/events/{event_id}/feed", headers=auth(user)).json()
    assert feed["solves"][0]["display_name"] == "Feedy"
    assert "flag" not in str(feed).lower() or "VOLT{" not in str(feed)
    act = client.get(f"/api/events/{event_id}/activity", headers=auth(user)).json()
    assert act["active_players"] == 1


def test_admin_stats_export_audit(client):
    admin = register(client, admin=True)
    user = register(client, name="Stats")
    event_id = make_event(client, admin)
    _enroll(client, user, event_id)
    s = start_session(client, user, "l1", "ranked", event_id)
    providers.set_mock_responder(refusing_responder)
    send(client, user, s["game_session_id"], "try one")
    providers.set_mock_responder(jailbroken_responder)
    send(client, user, s["game_session_id"], "win")

    stats = client.get(f"/api/admin/events/{event_id}/stats",
                       headers=auth(admin)).json()
    l1 = next(l for l in stats["levels"] if l["challenge_id"] == "l1")
    assert l1["starts"] == 1 and l1["solves"] == 1 and l1["attempts"] == 1
    assert l1["model_turns"] == 2 and l1["p50_latency_ms"] is not None

    csv_text = client.get(f"/api/admin/events/{event_id}/export",
                          headers=auth(admin)).text
    assert "Stats" in csv_text and "l1" in csv_text

    audit = client.get("/api/admin/audit", headers=auth(admin)).json()
    actions = {e["action"] for e in audit["entries"]}
    assert {"event.create", "event.challenge.set", "event.export"} <= actions
    # players cannot read audit or stats
    assert client.get("/api/admin/audit", headers=auth(user)).status_code == 403
    assert client.get(f"/api/admin/events/{event_id}/stats",
                      headers=auth(user)).status_code == 403


def test_challenge_version_pinning(client):
    """Ranked events keep the version pinned at configuration time even when
    a newer version is published later."""
    admin = register(client, admin=True)
    user = register(client)
    event_id = make_event(client, admin)          # pins l1 v1
    _enroll(client, user, event_id)

    cfg = _db(lambda c: c.fetchval(
        "SELECT config FROM challenge_versions WHERE challenge_id='l1' AND version=1"))
    import json
    cfg = json.loads(cfg)
    cfg["title"] = "L1 v2 TITLE"
    r = client.post("/api/admin/challenges/publish", headers=auth(admin),
                    json={"challenge_id": "l1", "config": cfg})
    assert r.status_code == 201 and r.json()["version"] == 2

    # ranked session still sees v1
    s = start_session(client, user, "l1", "ranked", event_id)
    assert s["challenge"]["version"] == 1
    # practice follows latest published
    sp = start_session(client, user, "l1", "practice")
    assert sp["challenge"]["version"] == 2
    assert sp["challenge"]["title"] == "L1 v2 TITLE"


def test_no_flag_or_prompt_leaks_in_player_payloads(client):
    """Flags and system prompts must never appear in any player-facing
    payload other than a genuine model reply."""
    user = register(client)
    providers.set_mock_responder(refusing_responder)
    s = start_session(client, user, "l1")
    send(client, user, s["game_session_id"], "hi")
    flag = _db(lambda c: c.fetchval("SELECT flag FROM player_flags LIMIT 1"))
    for path in ("/api/practice/challenges", "/api/me/progress",
                 f"/api/game/sessions/{s['game_session_id']}"):
        body = client.get(path, headers=auth(user)).text
        assert flag not in body
        assert "system_prompt" not in body
        assert "RULES:" not in body


def test_event_detail_challenges_for_player_and_admin(client):
    """Regression: event detail must list challenges for enrolled players AND
    for (unenrolled) admins managing the event."""
    admin = register(client, admin=True)
    user = register(client)
    event_id = make_event(client, admin, challenges=("l1", "l2"), points=(100, 200))
    # unenrolled player: event visible, challenges hidden
    d = client.get(f"/api/events/{event_id}", headers=auth(user)).json()
    assert d["challenges"] == [] and d["event"]["enrolled"] is False
    # enrolled player: challenges listed with points
    _enroll(client, user, event_id)
    d = client.get(f"/api/events/{event_id}", headers=auth(user)).json()
    assert [(c["challenge_id"], c["points"]) for c in d["challenges"]] == [
        ("l1", 100), ("l2", 200)]
    assert all(c["available"] for c in d["challenges"])
    # admin, not enrolled: full challenge list for management
    d = client.get(f"/api/events/{event_id}", headers=auth(admin)).json()
    assert len(d["challenges"]) == 2
