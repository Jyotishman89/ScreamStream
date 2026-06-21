import json
import time


def test_review_create_and_delete(client, register_and_login, token, first_movie_id):
    register_and_login()
    mid = first_movie_id
    r = client.post(f"/review/{mid}", data={
        "_csrf": token(f"/watch/{mid}"), "rating": "4",
        "body": "Solid flick."}, follow_redirects=True)
    assert "Solid flick." in r.get_data(as_text=True)
    r = client.post(f"/review/{mid}", data={
        "_csrf": token(f"/watch/{mid}"), "rating": "9"},
        follow_redirects=True)
    assert "1 to 5" in r.get_data(as_text=True)
    r = client.post(f"/review/{mid}/delete", data={
        "_csrf": token(f"/watch/{mid}")}, follow_redirects=True)
    assert "Solid flick." not in r.get_data(as_text=True)


def test_watchlist_toggle(client, register_and_login, token, first_movie_id):
    register_and_login()
    mid = first_movie_id
    client.post(f"/watchlist/toggle/{mid}", data={
        "_csrf": token(f"/watch/{mid}"), "next": "/watchlist"},
        follow_redirects=True)
    assert mid in client.get("/watchlist").get_data(as_text=True)


def test_change_password_invalidates_old(client, register_and_login, token):
    register_and_login("dave")
    r = client.post("/account/password", data={
        "_csrf": token("/account"), "current": "Str0ng!Pass9x",
        "password": "New!Pass123z", "confirm": "New!Pass123z"},
        follow_redirects=True)
    assert "signed out" in r.get_data(as_text=True).lower()
    client.post("/logout", data={"_csrf": token("/account")},
                follow_redirects=True)
    r = client.post("/login", data={
        "_csrf": token("/login"), "username": "dave",
        "password": "Str0ng!Pass9x"}, follow_redirects=True)
    assert "incorrect" in r.get_data(as_text=True)


def test_export_data_json(client, register_and_login):
    register_and_login("erin")
    r = client.get("/account/export")
    assert r.status_code == 200
    assert "attachment" in r.headers.get("Content-Disposition", "")
    data = json.loads(r.get_data(as_text=True))
    assert set(data) >= {"account", "history", "watchlist", "reviews"}


def test_delete_account(client, register_and_login, token, appmodule):
    register_and_login("frank")
    r = client.post("/account/delete", data={
        "_csrf": token("/account"), "password": "Str0ng!Pass9x"},
        follow_redirects=True)
    assert "permanently deleted" in r.get_data(as_text=True)
    with appmodule.app.app_context():
        n = appmodule.get_db().execute(
            "SELECT COUNT(*) AS n FROM users WHERE username = ?",
            ("frank",)).fetchone()["n"]
    assert n == 0


def test_totp_math_roundtrip(appmodule):
    secret = appmodule.generate_totp_secret()
    code = appmodule._hotp(secret, int(time.time() // 30))
    assert appmodule.verify_totp(secret, code) is True
    assert appmodule.verify_totp(secret, "000000") in (True, False)
    assert appmodule.verify_totp(secret, "abc") is False


def test_2fa_enable_and_login(client, register_and_login, token, appmodule):
    register_and_login("grace")
    client.get("/account/2fa/setup")
    with client.session_transaction() as sess:
        secret = sess["pending_totp_secret"]
    code = appmodule._hotp(secret, int(time.time() // 30))
    r = client.post("/account/2fa/enable", data={
        "_csrf": token("/account/2fa/setup"), "code": code},
        follow_redirects=True)
    assert "now on" in r.get_data(as_text=True)
    client.post("/logout", data={"_csrf": token("/account")},
                follow_redirects=True)
    r = client.post("/login", data={
        "_csrf": token("/login"), "username": "grace",
        "password": "Str0ng!Pass9x"}, follow_redirects=True)
    assert "Two-step verification" in r.get_data(as_text=True)
    code = appmodule._hotp(secret, int(time.time() // 30))
    r = client.post("/login/2fa", data={
        "_csrf": token("/login/2fa"), "code": code}, follow_redirects=True)
    assert client.get("/account").status_code == 200


def test_email_disabled_forgot_page(client):
    r = client.get("/forgot")
    assert r.status_code == 200


def test_email_enabled_verify_flow(client, register_and_login, token,
                                   appmodule, monkeypatch):
    captured = {}

    def fake_send(to, subject, body):
        captured["body"] = body
        return True

    monkeypatch.setattr(appmodule, "_send_email", fake_send)
    monkeypatch.setattr(appmodule, "EMAIL_ENABLED", True)
    register_and_login("heidi")
    r = client.post("/account/email", data={
        "_csrf": token("/account"), "email": "heidi@example.com"},
        follow_redirects=True)
    assert "Check your inbox" in r.get_data(as_text=True)
    import re
    link = re.search(r"/verify-email/\S+", captured["body"]).group(0)
    r = client.get(link, follow_redirects=True)
    assert "confirmed" in r.get_data(as_text=True)


def test_theme_toggle(client, register_and_login, token):
    register_and_login()
    assert 'data-theme="dark"' in client.get("/").get_data(as_text=True)
    r = client.post("/theme", data={"_csrf": token("/"), "next": "/"})
    assert "theme=light" in r.headers.get("Set-Cookie", "")
    assert 'data-theme="light"' in client.get("/").get_data(as_text=True)


def test_pwa_assets(client):
    sw = client.get("/sw.js")
    assert sw.status_code == 200
    assert sw.headers.get("Service-Worker-Allowed") == "/"
    assert client.get("/static/manifest.webmanifest").status_code == 200
    assert client.get("/static/icons/icon-192.png").status_code == 200
