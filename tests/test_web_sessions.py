from starsort.web import sessions

SECRET = "test-secret"
NOW = 1_800_000_000


def test_sign_and_verify_round_trip():
    token = sessions.sign({"login": "octo"}, SECRET, "session", max_age=60, now=NOW)

    payload = sessions.verify(token, SECRET, "session", now=NOW + 30)

    assert payload["login"] == "octo"


def test_expired_token_is_rejected():
    token = sessions.sign({"login": "octo"}, SECRET, "session", max_age=60, now=NOW)

    assert sessions.verify(token, SECRET, "session", now=NOW + 61) is None


def test_wrong_secret_is_rejected():
    token = sessions.sign({"login": "octo"}, SECRET, "session", max_age=60, now=NOW)

    assert sessions.verify(token, "other-secret", "session", now=NOW) is None


def test_edited_payload_is_rejected():
    token = sessions.sign({"login": "octo"}, SECRET, "session", max_age=60, now=NOW)
    forged_body = sessions.sign({"login": "admin"}, "attacker", "session", max_age=60, now=NOW).split(".")[0]

    assert sessions.verify(f"{forged_body}.{token.split('.')[1]}", SECRET, "session", now=NOW) is None


def test_token_for_another_purpose_is_rejected():
    login_token = sessions.sign({"state": "abc"}, SECRET, "login", max_age=60, now=NOW)

    assert sessions.verify(login_token, SECRET, "session", now=NOW) is None


def test_garbage_is_rejected():
    for token in ["", "abc", "abc.def", "....", "abc.dé"]:
        assert sessions.verify(token, SECRET, "session", now=NOW) is None


def test_read_cookie_finds_the_right_one():
    cookies = ["theme=dark", "starsort_session=abc.def; other=1"]

    assert sessions.read_cookie(cookies, "starsort_session") == "abc.def"
    assert sessions.read_cookie(cookies, "missing") is None
    assert sessions.read_cookie(None, "starsort_session") is None


def test_cookies_are_locked_down():
    cookie = sessions.make_cookie("starsort_session", "abc", 60)

    assert cookie.startswith("starsort_session=abc;")
    for flag in ("HttpOnly", "Secure", "SameSite=Lax", "Max-Age=60"):
        assert flag in cookie
    assert "Max-Age=0" in sessions.clear_cookie("starsort_session")
