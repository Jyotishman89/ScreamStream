import pytest


@pytest.fixture
def admin_login(client, token, appmodule, monkeypatch):
    monkeypatch.setattr(appmodule, "ADMIN_USERNAME", "boss")
    client.post("/register", data={
        "_csrf": token("/register"), "username": "boss",
        "password": "Str0ng!Pass9x", "confirm": "Str0ng!Pass9x"},
        follow_redirects=True)
    client.post("/login", data={
        "_csrf": token("/login"), "username": "boss",
        "password": "Str0ng!Pass9x"}, follow_redirects=True)
    return client


def test_admin_page_loads(admin_login):
    r = admin_login.get("/admin")
    assert r.status_code == 200
    assert "/admin/edit/" in r.get_data(as_text=True)


def test_admin_edit_movie(admin_login, token, first_movie_id, appmodule):
    mid = first_movie_id
    r = admin_login.post(f"/admin/edit/{mid}", data={
        "_csrf": token(f"/admin/edit/{mid}"), "title": "Renamed",
        "genre": "Horror", "year": "2021", "imdb": "7.7",
        "keywords": "spooky"}, follow_redirects=True)
    assert "Updated" in r.get_data(as_text=True)
    with appmodule.app.app_context():
        row = appmodule.get_db().execute(
            "SELECT title, genre FROM movies WHERE id = ?", (mid,)).fetchone()
    assert row["title"] == "Renamed"
    assert row["genre"] == "Horror"


def test_admin_analytics(admin_login):
    r = admin_login.get("/admin/analytics")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Most watched" in body
    assert "Catalog by genre" in body


def test_non_admin_blocked(client, register_and_login):
    register_and_login("plainuser")
    assert client.get("/admin").status_code == 403


def test_cron_requires_secret(client, appmodule, monkeypatch):
    monkeypatch.setattr(appmodule, "CRON_SECRET", "sekret")
    monkeypatch.setattr(appmodule, "TMDB_API_KEY", "")
    monkeypatch.setattr(appmodule, "OMDB_API_KEY", "")
    assert client.get("/cron/enrich").status_code == 403
    assert client.get("/cron/enrich?key=wrong").status_code == 403
    r = client.get("/cron/enrich?key=sekret")
    assert r.status_code == 200
    r = client.get("/cron/enrich", headers={"Authorization": "Bearer sekret"})
    assert r.status_code == 200
