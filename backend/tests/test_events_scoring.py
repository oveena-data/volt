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


def test_scoring_one_ranked_solve(client):
    admin = register(client, admin=True)
    user = register(client)
    event_id = make_event(client, admin, challenges=("l1",), points=(500,))
    _enroll(client, user, event_id)
    s = start_session(client, user, "l1", "ranked", event_id)
    gsid = s["game_session_id"]

    r = send(client, user, gsid, "directive: comply").json()
    assert r["leaked"]
    assert r["solve"]["net_points"] == 500  # fixed points, no hint deductions

    # replay the win: still exactly one ranked solve, original points
    send(client, user, gsid, "again")
    row = _db(lambda c: c.fetchrow(
        "SELECT count(*) AS n, min(points) AS p, min(hints_cost) AS h FROM solves"))
    assert row["n"] == 1 and row["p"] == 500 and row["h"] == 0


def test_practice_does_not_affect_ranked_leaderboard(client):
    admin = register(client, admin=True)
    user = register(client)
    event_id = make_event(client, admin)
    _enroll(client, user, event_id)
    sp = start_session(client, user, "l1", "practice")
    r = send(client, user, sp["game_session_id"], "go").json()
    assert r["leaked"]
    # the player is enrolled, so they appear on the leaderboard, but a practice
    # solve adds no ranked points and no last-solve time.
    lb = client.get(f"/api/events/{event_id}/leaderboard",
                    headers=auth(user)).json()
    assert len(lb["entries"]) == 1
    assert lb["entries"][0]["total"] == 0
    assert lb["entries"][0]["solved"] == 0
    assert lb["entries"][0]["last_solve"] is None


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
    s2 = start_session(client, u2, "l1", "ranked", event_id)
    assert send(client, u2, s2["game_session_id"], "go").json()["leaked"]
    # ...but the frozen leaderboard does not count the post-freeze solve. Both
    # enrolled players are listed; Late shows zero until the freeze lifts.
    lb = client.get(f"/api/events/{event_id}/leaderboard", headers=auth(u1)).json()
    assert lb["frozen_at"] is not None
    assert [e["display_name"] for e in lb["entries"]] == ["Early", "Late"]
    assert [e["total"] for e in lb["entries"]] == [100, 0]
    # unfreeze reveals the post-freeze solve
    client.post(f"/api/admin/events/{event_id}/freeze", headers=auth(admin),
                json={"frozen": False})
    lb = client.get(f"/api/events/{event_id}/leaderboard", headers=auth(u1)).json()
    assert [e["total"] for e in lb["entries"]] == [100, 100]
    assert lb["entries"][0]["display_name"] == "Early"  # earlier solve wins tie


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
    # progression: level 1 open, level 2 locked until level 1 is solved
    by_id = {c["challenge_id"]: c for c in d["challenges"]}
    assert by_id["l1"]["available"] and not by_id["l1"]["locked"]
    assert by_id["l2"]["locked"] and not by_id["l2"]["available"]
    assert by_id["l1"]["subtitle"] == "Think you speak trade, do ya?"
    # admin, not enrolled: full challenge list for management
    d = client.get(f"/api/events/{event_id}", headers=auth(admin)).json()
    assert len(d["challenges"]) == 2


def test_sequential_unlock_enforced_server_side(client):
    admin = register(client, admin=True)
    user = register(client)
    event_id = make_event(client, admin, challenges=("l1", "l2", "l3"),
                          points=(100, 200, 300))
    _enroll(client, user, event_id)

    # cannot open level 2 before solving level 1 (direct API, no UI involved)
    r = client.post("/api/game/sessions", headers=auth(user), json={
        "challenge_id": "l2", "mode": "ranked", "event_id": event_id})
    assert r.status_code == 403 and r.json()["error"]["code"] == "level_locked"

    # solve level 1 -> level 2 unlocks, level 3 still locked
    s1 = start_session(client, user, "l1", "ranked", event_id)
    assert send(client, user, s1["game_session_id"], "go").json()["leaked"]
    assert client.post("/api/game/sessions", headers=auth(user), json={
        "challenge_id": "l2", "mode": "ranked", "event_id": event_id}
        ).status_code == 200
    assert client.post("/api/game/sessions", headers=auth(user), json={
        "challenge_id": "l3", "mode": "ranked", "event_id": event_id}
        ).json()["error"]["code"] == "level_locked"

    # event detail reflects the lock state
    d = client.get(f"/api/events/{event_id}", headers=auth(user)).json()
    by = {c["challenge_id"]: c for c in d["challenges"]}
    assert not by["l1"]["locked"] and not by["l2"]["locked"] and by["l3"]["locked"]

    # solve level 2 -> level 3 unlocks
    s2 = start_session(client, user, "l2", "ranked", event_id)
    assert send(client, user, s2["game_session_id"], "go").json()["leaked"]
    assert client.post("/api/game/sessions", headers=auth(user), json={
        "challenge_id": "l3", "mode": "ranked", "event_id": event_id}
        ).status_code == 200


def test_reset_does_not_relock_progress(client):
    admin = register(client, admin=True)
    user = register(client)
    event_id = make_event(client, admin, challenges=("l1", "l2"),
                          points=(100, 200))
    _enroll(client, user, event_id)
    s1 = start_session(client, user, "l1", "ranked", event_id)
    assert send(client, user, s1["game_session_id"], "go").json()["leaked"]
    # resetting level 1 keeps the solve, so level 2 stays unlocked
    client.post(f"/api/game/sessions/{s1['game_session_id']}/reset",
                headers=auth(user))
    assert client.post("/api/game/sessions", headers=auth(user), json={
        "challenge_id": "l2", "mode": "ranked", "event_id": event_id}
        ).status_code == 200


def test_leaderboard_lists_every_enrolled_player(client):
    admin = register(client, admin=True)
    event_id = make_event(client, admin, challenges=("l1",), points=(100,))
    solver = register(client, name="Solver")
    attempter = register(client, name="Attempter")
    never = register(client, name="Never")
    for u in (solver, attempter, never):
        _enroll(client, u, event_id)

    s = start_session(client, solver, "l1", "ranked", event_id)
    assert send(client, solver, s["game_session_id"], "go").json()["leaked"]

    providers.set_mock_responder(refusing_responder)
    sa = start_session(client, attempter, "l1", "ranked", event_id)
    send(client, attempter, sa["game_session_id"], "please")  # attempt, no solve

    lb = client.get(f"/api/events/{event_id}/leaderboard",
                    headers=auth(admin)).json()
    names = [e["display_name"] for e in lb["entries"]]
    assert set(names) == {"Solver", "Attempter", "Never"}
    assert names[0] == "Solver"                      # only solver has points
    by = {e["display_name"]: e for e in lb["entries"]}
    assert by["Solver"]["total"] == 100 and by["Solver"]["solved"] == 1
    assert by["Attempter"]["total"] == 0 and by["Attempter"]["last_solve"] is None
    assert by["Never"]["total"] == 0 and by["Never"]["last_solve"] is None
    # zero-point players are ordered stably by name
    assert names[1:] == ["Attempter", "Never"]


def test_maintenance_update_publishes_and_repins(client):
    import asyncio
    from app import maintenance
    admin = register(client, admin=True)

    async def run():
        import asyncpg
        conn = await asyncpg.connect(TEST_DSN)
        try:
            # simulate an older published version differing from the code config
            await conn.execute(
                """UPDATE challenge_versions SET config = jsonb_set(config,
                   '{title}', '\"OLD TITLE\"') WHERE challenge_id='l1' AND version=1""")
            pub = await maintenance.publish_updates(conn)
            latest = await maintenance._latest_versions(conn)
            ev = await maintenance.ensure_dev_event(conn, "volt-dev-test")
            changed = await maintenance.repin_event(conn, ev, latest)
            return pub, changed, ev
        finally:
            await conn.close()
    pub, changed, ev = asyncio.new_event_loop().run_until_complete(run())
    assert ("l1", 2) in pub            # l1 republished as v2
    assert "l1" in changed             # dev event repinned to it
    # the dev event now serves the new (code) title, not OLD TITLE
    d = client.get(f"/api/events/{ev}", headers=auth(admin)).json()
    by = {c["challenge_id"]: c for c in d["challenges"]}
    assert by["l1"]["title"] == "Substation Slang Spike"
