"""End-to-end API flow: boot, setup wizard, auth, protected routes, file browser,
settings. Runs as one ordered flow so the shared session/DB stays coherent."""


def test_full_flow(client):
    # Health is public.
    assert client.get("/api/health").json()["status"] == "ok"

    # Fresh install needs setup and blocks protected routes.
    assert client.get("/api/auth/status").json() == {"setup_needed": True, "authenticated": False}
    assert client.get("/api/downloads/tasks").status_code == 401

    # First-run setup creates the admin and logs in.
    r = client.post("/api/auth/setup", json={"username": "admin", "password": "supersecret1"})
    assert r.status_code == 200
    assert client.get("/api/auth/me").json()["username"] == "admin"

    # Setup cannot run twice.
    assert client.post("/api/auth/setup",
                       json={"username": "other", "password": "supersecret1"}).status_code == 409

    # Password policy is enforced (too short -> 422 before handler).
    assert client.post("/api/auth/setup", json={"username": "x", "password": "short"}).status_code == 422

    # Tasks endpoint is reachable once authenticated.
    t = client.get("/api/downloads/tasks").json()
    assert t["active"] == 0 and t["concurrency"] == 2

    # File browser starts at the root.
    root = client.get("/api/files").json()
    assert root["is_root"] and root["rel"] == "/"

    # Creating a folder switches the download dir to it.
    made = client.post("/api/files/mkdir", json={"parent": root["path"], "name": "movies"}).json()
    assert made["is_current"] and made["current_rel"] == "/movies"

    # Path traversal outside the root is refused.
    assert client.post("/api/files/set-current", json={"path": "/etc"}).status_code == 400

    # Settings reflect defaults; mihomo is disabled in tests.
    s = client.get("/api/settings").json()
    assert s["proxy"]["mode"] == "off" and s["bot"]["enabled"] is False

    # Telegram is not configured yet.
    tg = client.get("/api/telegram/status").json()
    assert tg["credentials_ready"] is False and tg["authorized"] is False

    # Concurrency change round-trips and clamps.
    assert client.post("/api/downloads/concurrency", json={"value": 4}).json()["value"] == 4
    assert client.post("/api/downloads/concurrency", json={"value": 99}).json()["value"] == 8

    # Logout invalidates the session.
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/auth/me").status_code == 401


def test_login_after_logout(client):
    # The account created above can log back in.
    assert client.post("/api/auth/login",
                       json={"username": "admin", "password": "supersecret1"}).status_code == 200
    assert client.get("/api/auth/me").json()["username"] == "admin"
    # Wrong password is rejected.
    client.post("/api/auth/logout")
    assert client.post("/api/auth/login",
                       json={"username": "admin", "password": "nope"}).status_code == 401


def test_backend_i18n(client):
    # Server-generated messages follow the stored language.
    client.post("/api/auth/login", json={"username": "admin", "password": "supersecret1"})

    client.post("/api/settings/language", json={"lang": "zh"})
    r = client.post("/api/downloads/link", json={"link": "https://t.me/x/1"}).json()
    assert r["ok"] is False and r["error"] == "用户端尚未登录", r

    client.post("/api/settings/language", json={"lang": "en"})
    r = client.post("/api/downloads/link", json={"link": "https://t.me/x/1"}).json()
    assert r["ok"] is False and r["error"] == "Telegram user client is not logged in", r

    # An unsupported language is rejected.
    assert client.post("/api/settings/language", json={"lang": "fr"}).json()["ok"] is False
    client.post("/api/auth/logout")
