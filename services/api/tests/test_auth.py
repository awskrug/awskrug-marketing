import json
import urllib.parse

import pytest

from marketing_api import auth, common
from conftest import SECRET

KEY = SECRET["sessionSigningKey"]


class FakeGitHub:
    """auth.set_http에 넣는 가짜. 요청 기록을 남기고 URL별로 응답한다."""

    def __init__(self, member=("200", "active"), token_resp=None):
        self.calls = []
        self.member = member
        self.token_resp = token_resp or (200, {"access_token": "ghu_SECRETTOKEN", "token_type": "bearer"})
        self.user = {"id": 42, "login": "octo", "name": "Octo Cat", "avatar_url": "http://a/1"}

    def __call__(self, method, url, headers=None, json_body=None):
        self.calls.append((method, url, headers or {}, json_body))
        if url == auth.TOKEN_URL:
            return self.token_resp
        if url == auth.API_BASE + "/user":
            return 200, self.user
        if "/memberships/" in url:
            code, state = self.member
            return (404, {"message": "Not Found"}) if code == "404" else (int(code), {"state": state, "role": "member"})
        raise AssertionError(url)


@pytest.fixture
def gh(aws):
    fake = FakeGitHub()
    auth.set_http(fake)
    yield fake
    auth.set_http(None)


def cb(state="S", cookie_state="S", code="c0de", **extra):
    ev = {"routeKey": "GET /api/auth/callback", "queryStringParameters": {"code": code, "state": state}}
    if cookie_state is not None:
        ev["cookies"] = [f"oauth_state={cookie_state}"]
    ev["queryStringParameters"].update(extra)
    return ev


def run(ev):
    return auth.handler(ev, None)


def test_login_redirect_and_cookie(aws):
    out = run({"routeKey": "GET /api/auth/login"})
    assert out["statusCode"] == 302
    url = urllib.parse.urlparse(out["headers"]["Location"])
    assert (url.netloc, url.path) == ("github.com", "/login/oauth/authorize")
    q = urllib.parse.parse_qs(url.query)
    assert q["client_id"] == ["cid"] and q["redirect_uri"] == ["https://site.example/api/auth/callback"]
    (cookie,) = out["cookies"]
    assert cookie.startswith(f"oauth_state={q['state'][0]};")
    for attr in ["HttpOnly", "Secure", "SameSite=Lax", "Max-Age=600"]:
        assert attr in cookie
    assert "csecret" not in json.dumps(out)


def test_login_state_is_random(aws):
    a, b = (run({"routeKey": "GET /api/auth/login"})["cookies"][0] for _ in range(2))
    assert a != b


def test_callback_member_sets_session(gh, aws):
    out = run(cb())
    assert out["statusCode"] == 302 and out["headers"]["Location"] == "https://site.example/"
    session = next(c for c in out["cookies"] if c.startswith("session="))
    for attr in ["HttpOnly", "Secure", "SameSite=Lax", f"Max-Age={common.SESSION_TTL_SECONDS}"]:
        assert attr in session
    payload = common.jwt_verify(session.split(";")[0].split("=", 1)[1], KEY)
    assert payload["sub"] == "42" and payload["login"] == "octo"
    assert payload["exp"] == 1_800_000_000 + common.SESSION_TTL_SECONDS
    assert any(c.startswith("oauth_state=;") and "Max-Age=0" in c for c in out["cookies"])


def test_callback_github_requests(gh, aws):
    run(cb())
    method, url, headers, body = gh.calls[0]
    assert (method, url, headers["Accept"]) == ("POST", auth.TOKEN_URL, "application/json")
    assert body == {"client_id": "cid", "client_secret": "csecret", "code": "c0de",
                    "redirect_uri": "https://site.example/api/auth/callback"}
    assert gh.calls[1][2]["Authorization"] == "Bearer ghu_SECRETTOKEN"
    assert gh.calls[2][1] == "https://api.github.com/orgs/awskrug/teams/sns-working-group/memberships/octo"


def test_callback_upserts_user_and_keeps_first_login(gh, aws):
    users = aws.dynamo.Table("Users")
    run(cb())
    row = dict(users.items["42"])
    assert row["login"] == "octo" and row["name"] == "Octo Cat" and row["avatarUrl"] == "http://a/1"
    assert row["firstLoginAt"] == row["lastLoginAt"] == "2027-01-15T08:00:00Z"
    users.items["42"]["firstLoginAt"] = "2026-01-01T00:00:00Z"
    run(cb())
    assert users.items["42"]["firstLoginAt"] == "2026-01-01T00:00:00Z"


@pytest.mark.parametrize("member", [("404", None), ("200", "pending")])
def test_non_member_rejected(gh, aws, member):
    gh.member = member
    out = run(cb())
    assert out["statusCode"] == 302 and out["headers"]["Location"] == "https://site.example/?error=not_member"
    assert not any(c.startswith("session=") for c in out.get("cookies", []))
    assert aws.dynamo.Table("Users").items == {}


def test_membership_error_is_502_not_member_login(gh, aws):
    gh.member = ("500", None)
    out = run(cb())
    assert out["statusCode"] == 502 and aws.dynamo.Table("Users").items == {}
    assert "session=" not in json.dumps(out.get("cookies", []))


@pytest.mark.parametrize("ev", [
    cb(state="X", cookie_state="S"),
    cb(cookie_state=None),
    cb(state=""),
    {"routeKey": "GET /api/auth/callback", "queryStringParameters": {"code": "c"}, "cookies": ["oauth_state=S"]},
    {"routeKey": "GET /api/auth/callback"},
])
def test_state_mismatch_or_missing_400(gh, aws, ev):
    out = run(ev)
    assert out["statusCode"] == 400
    assert gh.calls == [] and aws.dynamo.Table("Users").items == {}
    assert not any(c.startswith("session=") for c in out.get("cookies", []))


def test_missing_code_400(gh, aws):
    ev = cb()
    del ev["queryStringParameters"]["code"]
    assert run(ev)["statusCode"] == 400 and gh.calls == []


def test_github_denied_redirects(gh, aws):
    ev = cb(error="access_denied")
    del ev["queryStringParameters"]["code"]
    out = run(ev)
    assert out["statusCode"] == 302 and "error=login_failed" in out["headers"]["Location"]
    assert gh.calls == []


def test_bad_code_400_and_no_token_leak(gh, aws):
    gh.token_resp = (200, {"error": "bad_verification_code"})
    out = run(cb())
    assert out["statusCode"] == 400 and aws.dynamo.Table("Users").items == {}


def test_github_unreachable_502(gh, aws):
    gh.token_resp = (502, None)
    assert run(cb())["statusCode"] == 502


def test_no_secrets_or_tokens_in_responses_or_logs(gh, aws, capsys):
    for ev in (cb(), cb(state="bad")):
        out = run(ev)
        blob = json.dumps(out)
        assert "ghu_SECRETTOKEN" not in blob and "csecret" not in blob and KEY not in blob
    gh.token_resp = (500, None)
    run(cb())
    cap = capsys.readouterr()
    assert "ghu_SECRETTOKEN" not in cap.out + cap.err and "csecret" not in cap.out + cap.err


def test_unexpected_exception_is_generic_500(gh, aws, capsys):
    gh.user = None  # _fetch_user가 502를 낸다
    assert run(cb())["statusCode"] == 502
    aws.dynamo.tables.clear()
    auth.set_http(lambda *a, **k: (_ for _ in ()).throw(RuntimeError("tok ghu_X")))
    out = run(cb())
    assert out["statusCode"] == 500 and "ghu_X" not in json.dumps(out) + capsys.readouterr().out


def test_logout(aws):
    out = run({"routeKey": "POST /api/auth/logout"})
    assert out["statusCode"] == 204
    assert out["cookies"][0].startswith("session=;") and "Max-Age=0" in out["cookies"][0]


def test_membership_username_is_url_quoted(gh, aws):
    gh.user = {"id": 1, "login": "a/../b", "name": None, "avatar_url": None}
    run(cb())
    assert gh.calls[2][1].endswith("/memberships/a%2F..%2Fb")


@pytest.mark.parametrize("unset", [True, False])
@pytest.mark.parametrize("route", ["GET /api/auth/login", "GET /api/auth/callback"])
def test_site_url_not_configured_is_500_with_message(gh, aws, monkeypatch, route, unset):
    if unset:
        monkeypatch.delenv("SITE_URL")
    else:
        monkeypatch.setenv("SITE_URL", "")
    ev = cb() if "callback" in route else {"routeKey": route}
    out = run(ev)
    assert out["statusCode"] == 500
    assert "Location" not in out.get("headers", {})
    assert "SITE_URL" in json.loads(out["body"])["error"]
    assert gh.calls == []


def test_logout_works_without_site_url(aws, monkeypatch):
    monkeypatch.delenv("SITE_URL")
    assert run({"routeKey": "POST /api/auth/logout"})["statusCode"] == 204


def test_logout_rejects_foreign_origin(aws):
    out = run({"routeKey": "POST /api/auth/logout", "headers": {"origin": "https://evil.example"}})
    assert out["statusCode"] == 403 and "cookies" not in out
    assert run({"routeKey": "POST /api/auth/logout", "headers": {"origin": "https://site.example"}})["statusCode"] == 204


def test_bad_secret_gives_clear_500_without_leak(aws):
    aws.secrets.secret = {"githubClientId": "a", "githubClientSecret": "b", "sessionSigningKey": "REPLACE_ME"}
    out = run({"routeKey": "GET /api/auth/login"})
    assert out["statusCode"] == 500 and "sessionSigningKey" in out["body"] and "REPLACE_ME" not in out["body"]


def test_network_error_to_github_fails_closed(gh, aws):
    gh.token_resp = (599, None)
    out = run(cb())
    assert out["statusCode"] == 502 and not any(c.startswith("session=") and "Max-Age=0" not in c for c in out.get("cookies", []))
