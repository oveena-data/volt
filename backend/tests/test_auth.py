"""Authentication, roles, ownership isolation."""

import uuid

from conftest import auth, register, send, start_session


def test_security_headers_on_every_response(client):
    r = client.get("/api/healthz")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["cache-control"] == "no-store"
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert "max-age" in r.headers["strict-transport-security"]


def test_register_login_me_logout(client):
    user = register(client, name="Ada")
    r = client.get("/api/auth/me", headers=auth(user))
    assert r.status_code == 200
    me = r.json()
    assert me["display_name"] == "Ada" and me["role"] == "player"

    # login issues a fresh token
    r = client.post("/api/auth/login", json={
        "email": user["email"], "password": user["password"]})
    assert r.status_code == 200
    tok2 = r.json()["token"]
    assert tok2 != user["token"]

    # logout revokes only the used token
    r = client.post("/api/auth/logout", headers=auth(user))
    assert r.status_code == 200
    assert client.get("/api/auth/me", headers=auth(user)).status_code == 401
    assert client.get("/api/auth/me",
                      headers={"Authorization": f"Bearer {tok2}"}).status_code == 200


def test_wrong_password_and_duplicate_email(client):
    user = register(client)
    r = client.post("/api/auth/login", json={
        "email": user["email"], "password": "wrong-password-123"})
    assert r.status_code == 401
    r = client.post("/api/auth/register", json={
        "email": user["email"], "password": "hunter2hunter2", "display_name": "X"})
    assert r.status_code == 409


def test_bad_tokens_rejected(client):
    for header in ({}, {"Authorization": "Bearer "},
                   {"Authorization": "Bearer not-a-real-token"},
                   {"Authorization": "Basic abc"}):
        assert client.get("/api/auth/me", headers=header).status_code == 401


def test_session_expiry(client):
    import asyncio

    import asyncpg

    from conftest import TEST_DSN
    user = register(client)

    async def expire():
        conn = await asyncpg.connect(TEST_DSN)
        await conn.execute(
            "UPDATE auth_sessions SET expires_at = now() - interval '1 minute'")
        await conn.close()
    asyncio.new_event_loop().run_until_complete(expire())
    assert client.get("/api/auth/me", headers=auth(user)).status_code == 401


def test_display_name_separate_from_identity(client):
    user = register(client, name="Before")
    r = client.patch("/api/auth/display-name", headers=auth(user),
                     json={"display_name": "After"})
    assert r.status_code == 200
    me = client.get("/api/auth/me", headers=auth(user)).json()
    assert me["display_name"] == "After"
    assert me["id"] == user["user"]["id"]  # immutable id unchanged


def test_admin_role_enforced_server_side(client):
    player = register(client)
    # player cannot touch admin APIs
    r = client.post("/api/admin/events", headers=auth(player), json={
        "slug": "nope", "name": "x",
        "starts_at": "2020-01-01T00:00:00Z", "ends_at": "2030-01-01T00:00:00Z"})
    assert r.status_code == 403
    assert client.get("/api/admin/audit", headers=auth(player)).status_code == 403
    # role comes from server config, not from anything client-supplied
    admin = register(client, admin=True)
    assert admin["user"]["role"] == "admin"


def test_cross_player_session_isolation(client):
    alice, bob = register(client), register(client)
    s = start_session(client, alice, "l1")
    gsid = s["game_session_id"]
    # Bob cannot read, message, reset, hint, or submit on Alice's session.
    assert client.get(f"/api/game/sessions/{gsid}",
                      headers=auth(bob)).status_code == 404
    assert send(client, bob, gsid, "hello").status_code == 404
    assert client.post(f"/api/game/sessions/{gsid}/reset",
                       headers=auth(bob)).status_code == 404
    assert client.post(f"/api/game/sessions/{gsid}/hints", headers=auth(bob),
                       json={"hint_index": 0}).status_code == 404
    assert client.post(f"/api/game/sessions/{gsid}/submit", headers=auth(bob),
                       json={"flag": "VOLT{x}"}).status_code == 404
    # Nonexistent session id: same 404, no information leak
    assert client.get(f"/api/game/sessions/{uuid.uuid4()}",
                      headers=auth(alice)).status_code == 404
    assert client.get("/api/game/sessions/not-a-uuid",
                      headers=auth(alice)).status_code == 404


def test_cors_preflight_allows_every_method_the_api_serves(client):
    """A method the router serves but CORS omits is invisible to the test
    client (which bypasses preflight) and fails only in a real browser."""
    from starlette.routing import Route

    from app.main import app, middleware
    served = {m for r in app.routes if isinstance(r, Route)
              for m in (r.methods or set())} - {"HEAD"}
    cors = next(mw for mw in middleware if "CORS" in mw.cls.__name__)
    allowed = set(cors.kwargs["allow_methods"])
    assert served <= allowed, f"CORS blocks served methods: {served - allowed}"
