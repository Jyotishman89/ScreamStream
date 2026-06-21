import os
import re

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret")

import app as appmod

TOKEN_RE = re.compile(r'name="_csrf" value="([^"]+)"')


def extract_token(html):
    match = TOKEN_RE.search(html)
    return match.group(1) if match else None


@pytest.fixture
def appmodule(tmp_path):
    appmod.DATABASE = str(tmp_path / "test.db")
    with appmod.app.app_context():
        appmod.init_db()
        appmod.seed_movies()
    return appmod


@pytest.fixture
def client(appmodule):
    return appmodule.app.test_client()


@pytest.fixture
def token(client):
    def _token(path="/login"):
        html = client.get(path).get_data(as_text=True)
        return extract_token(html)
    return _token


@pytest.fixture
def register_and_login(client, token):
    def _do(username="tester", password="Str0ng!Pass9x"):
        client.post("/register", data={
            "_csrf": token("/register"), "username": username,
            "password": password, "confirm": password}, follow_redirects=True)
        client.post("/login", data={
            "_csrf": token("/login"), "username": username,
            "password": password}, follow_redirects=True)
        return username, password
    return _do


@pytest.fixture
def first_movie_id(appmodule):
    with appmodule.app.app_context():
        row = appmodule.get_db().execute(
            "SELECT id FROM movies LIMIT 1").fetchone()
        return row["id"]
