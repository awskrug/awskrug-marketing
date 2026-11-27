"""GitHub App 사용자 로그인: `/api/auth/login`, `/api/auth/callback`, `/api/auth/logout`.

GitHub 동작은 2026-11-27에 공식 문서로 확인했다. 자세한 근거는 services/api/README.md에 있다.
"""

import hmac
import json
import os
import secrets
import urllib.error
import urllib.parse
import urllib.request

from . import common

AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
TOKEN_URL = "https://github.com/login/oauth/access_token"
API_BASE = "https://api.github.com"
STATE_MAX_AGE = 600  # oauth_state 쿠키 유효 시간 (10분)
HTTP_TIMEOUT = 10
CALLBACK_PATH = "/api/auth/callback"

_http = None


def http_request(method, url, headers=None, json_body=None):
    """GitHub로 나가는 유일한 HTTP 호출. (상태 코드, JSON 본문 또는 None)을 돌려준다."""
    data = json.dumps(json_body).encode() if json_body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=dict(headers or {}))
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            status, raw = resp.status, resp.read(1_000_000)
    except urllib.error.HTTPError as exc:
        status, raw = exc.code, exc.read(1_000_000)
    except (urllib.error.URLError, OSError):
        return 599, None  # 연결 실패나 시간 초과. 호출한 쪽이 502로 닫는다.
    try:
        return status, json.loads(raw) if raw else None
    except ValueError:
        return status, None


def set_http(fn):
    """테스트용: HTTP 호출 함수를 바꿔 끼운다. None이면 기본 구현으로 되돌린다."""
    global _http
    _http = fn


def _call(*args, **kwargs):
    return (_http or http_request)(*args, **kwargs)


SITE_URL_MISSING = ("SITE_URL이 설정되지 않아 로그인할 수 없습니다. "
                    "배포 때 CDK 컨텍스트 siteUrl 또는 domainName을 지정해 다시 배포하세요.")


def _site():
    """웹 주소. 설정되지 않았으면 빈 문자열. 상대 주소로는 GitHub redirect_uri를 만들 수 없다."""
    return (os.environ.get("SITE_URL") or "").strip().rstrip("/")


class AuthFailure(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def login(event):
    if not _site():
        return common.error(500, SITE_URL_MISSING)
    state = secrets.token_urlsafe(32)
    query = urllib.parse.urlencode({
        "client_id": common.load_secret()["githubClientId"],
        "redirect_uri": _site() + CALLBACK_PATH,
        "state": state,
    })
    return common.redirect(f"{AUTHORIZE_URL}?{query}",
                           cookies=[common.make_cookie(common.OAUTH_STATE_COOKIE, state, STATE_MAX_AGE)])


def _exchange_code(code):
    secret = common.load_secret()
    status, data = _call("POST", TOKEN_URL, {"Accept": "application/json"}, {
        "client_id": secret["githubClientId"], "client_secret": secret["githubClientSecret"],
        "code": code, "redirect_uri": _site() + CALLBACK_PATH,
    })
    if status >= 500 or not isinstance(data, dict):
        raise AuthFailure(502, "GitHub와 통신하지 못했습니다.")
    token = data.get("access_token")
    if status != 200 or not isinstance(token, str) or not token:
        raise AuthFailure(400, "GitHub 인증 코드가 올바르지 않습니다.")
    return token


def _github_get(path, token):
    return _call("GET", API_BASE + path, {
        "Accept": "application/vnd.github+json", "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "marketing-api"})


def _fetch_user(token):
    status, data = _github_get("/user", token)
    if status != 200 or not isinstance(data, dict) or not data.get("login") or data.get("id") is None:
        raise AuthFailure(502, "GitHub 사용자 정보를 읽지 못했습니다.")
    return data


def is_team_member(token, login):
    """사용자 본인의 GitHub App 사용자 토큰으로 팀 멤버십을 확인한다. state가 active일 때만 멤버다."""
    q = lambda s: urllib.parse.quote(s, safe="")
    path = f"/orgs/{q(os.environ['GITHUB_ORG'])}/teams/{q(os.environ['GITHUB_TEAM'])}/memberships/{q(login)}"
    status, data = _github_get(path, token)
    if status == 404:
        return False
    if status != 200 or not isinstance(data, dict):
        raise AuthFailure(502, "팀 멤버십을 확인하지 못했습니다.")
    return data.get("state") == "active"


def _upsert_user(user):
    stamp = common.utc_iso()
    common.get_table("USERS_TABLE").update_item(
        Key={"githubId": str(user["id"])},
        UpdateExpression=("SET login = :l, #n = :n, avatarUrl = :a, lastLoginAt = :t, "
                          "firstLoginAt = if_not_exists(firstLoginAt, :t)"),
        ExpressionAttributeNames={"#n": "name"},
        ExpressionAttributeValues={":l": user["login"], ":n": user.get("name"),
                                   ":a": user.get("avatar_url"), ":t": stamp})


def callback(event):
    if not _site():
        return common.error(500, SITE_URL_MISSING)
    params = event.get("queryStringParameters") or {}
    clear_state = common.clear_cookie(common.OAUTH_STATE_COOKIE)
    state, expected = params.get("state"), common.parse_cookies(event).get(common.OAUTH_STATE_COOKIE)
    if not state or not expected or not hmac.compare_digest(state.encode(), expected.encode()):
        return common.response(400, {"error": "로그인 상태 값이 맞지 않습니다. 다시 시도해 주세요."}, cookies=[clear_state])
    if params.get("error"):
        return common.redirect(_site() + "/?error=login_failed", cookies=[clear_state])
    code = params.get("code")
    if not code:
        return common.response(400, {"error": "code가 없습니다."}, cookies=[clear_state])
    try:
        token = _exchange_code(code)
        user = _fetch_user(token)
        if not is_team_member(token, user["login"]):
            return common.redirect(_site() + "/?error=not_member", cookies=[clear_state])
    except AuthFailure as exc:
        return common.response(exc.status, {"error": exc.message}, cookies=[clear_state])
    _upsert_user(user)
    session = common.jwt_sign(
        {"sub": str(user["id"]), "login": user["login"], "exp": int(common.now()) + common.SESSION_TTL_SECONDS},
        common.load_secret()["sessionSigningKey"])
    return common.redirect(_site() + "/", cookies=[
        common.make_cookie(common.SESSION_COOKIE, session, common.SESSION_TTL_SECONDS), clear_state])


def logout(event):
    blocked = common.check_post_request(event)
    if blocked:
        return common.error(*blocked)
    return {"statusCode": 204, "cookies": [common.clear_cookie(common.SESSION_COOKIE)]}


ROUTES = {
    "GET /api/auth/login": login,
    "GET /api/auth/callback": callback,
    "POST /api/auth/logout": logout,
}


def handler(event, context):
    route = ROUTES.get(event.get("routeKey"))
    if route is None:
        return common.error(404, "없는 경로입니다.")
    try:
        return route(event)
    except common.ConfigError as exc:
        print(f"config error on {event.get('routeKey')}")
        return common.error(500, str(exc))
    except Exception as exc:
        print(f"unhandled {type(exc).__name__} on {event.get('routeKey')}")
        return common.error(500, "서버 오류가 발생했습니다.")
