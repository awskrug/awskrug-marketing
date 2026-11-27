"""HTTP API Lambda 권한 부여자 (페이로드 2.0, 단순 응답)."""

from . import common


def _deny():
    return {"isAuthorized": False}


def handler(event, context):
    try:
        token = common.parse_cookies(event).get(common.SESSION_COOKIE)
        if not token:
            return _deny()
        payload = common.jwt_verify(token, common.load_secret()["sessionSigningKey"])
        sub, login = (payload or {}).get("sub"), (payload or {}).get("login")
        if not isinstance(sub, str) or not sub or not isinstance(login, str) or not login:
            return _deny()
        return {"isAuthorized": True, "context": {"githubId": sub, "login": login}}
    except Exception:
        return _deny()
