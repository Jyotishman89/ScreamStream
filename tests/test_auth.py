from conftest import extract_token


def test_register_and_login(client, token):
    r = client.post("/register", data={
        "_csrf": token("/register"), "username": "alice",
        "password": "Str0ng!Pass9x", "confirm": "Str0ng!Pass9x"},
        follow_redirects=True)
    assert r.status_code == 200
    r = client.post("/login", data={
        "_csrf": token("/login"), "username": "alice",
        "password": "Str0ng!Pass9x"}, follow_redirects=True)
    assert client.get("/account").status_code == 200


def test_weak_password_rejected(client, token):
    r = client.post("/register", data={
        "_csrf": token("/register"), "username": "bob",
        "password": "password", "confirm": "password"},
        follow_redirects=True)
    body = r.get_data(as_text=True)
    assert "/account" not in r.headers.get("Location", "")
    assert "register" in body.lower() or "password" in body.lower()


def test_numeric_username_rejected(client, token):
    r = client.post("/register", data={
        "_csrf": token("/register"), "username": "12345",
        "password": "Str0ng!Pass9x", "confirm": "Str0ng!Pass9x"},
        follow_redirects=True)
    assert "at least one letter" in r.get_data(as_text=True)


def test_login_required_redirect(client):
    r = client.get("/account")
    assert r.status_code in (301, 302)
    assert "/login" in r.headers.get("Location", "")


def test_csrf_blocks_forged_post(client, register_and_login):
    register_and_login()
    r = client.post("/logout", data={"_csrf": "bogus"}, follow_redirects=False)
    assert r.status_code in (302, 303)
    assert "/logout" not in r.headers.get("Location", "")


def test_wrong_password_message(client, register_and_login, token):
    register_and_login("carol")
    client.post("/logout", data={"_csrf": token("/account")},
                follow_redirects=True)
    r = client.post("/login", data={
        "_csrf": token("/login"), "username": "carol",
        "password": "nope"}, follow_redirects=True)
    assert "incorrect" in r.get_data(as_text=True)
