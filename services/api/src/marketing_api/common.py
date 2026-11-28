"""API Lambda들이 함께 쓰는 도우미: JSON 응답, 쿠키, HS256 JWT, 비밀 값, 시간, AWS 클라이언트."""

import base64
import hashlib
import hmac
import json
import os
import time
from datetime import datetime, timezone
from decimal import Decimal

SESSION_COOKIE = "session"
OAUTH_STATE_COOKIE = "oauth_state"
SESSION_TTL_SECONDS = 8 * 60 * 60  # 세션 JWT와 쿠키의 유효 시간 (8시간)

SECRET_TTL_SECONDS = 300  # 비밀 값 캐시 유효 시간. 교체한 비밀을 컨테이너가 5분 안에 반영한다.
MIN_SIGNING_KEY_LENGTH = 32
_PLACEHOLDER_KEYS = {"replace_me", "changeme", "change_me", "placeholder", "secret", "todo"}
SECRET_FIELDS = ("githubClientId", "githubClientSecret", "sessionSigningKey")

_clients = {}
_secret_cache = {}


class ConfigError(Exception):
    """배포 설정이나 비밀 값이 잘못됐다. 메시지에는 비밀 내용을 넣지 않는다."""


# ---- 시간 ----

def now():
    """현재 시각(epoch 초). 테스트에서 바꿔 끼운다."""
    return time.time()


def utc_iso():
    return datetime.fromtimestamp(now(), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---- AWS 클라이언트 (지연 생성, 주입 가능) ----

def set_client(name, client):
    """테스트용: name("s3", "dynamodb", "stepfunctions", "secretsmanager")의 클라이언트를 주입한다."""
    if client is None:
        _clients.pop(name, None)
    else:
        _clients[name] = client


def s3_client_kwargs(region=None):
    """미리 서명된 주소가 리전 주소(버킷.s3.<리전>.amazonaws.com)를 쓰게 하는 S3 클라이언트 설정.

    리전을 지정하지 않으면 boto3는 전역 주소(버킷.s3.amazonaws.com)로 서명한다. 전역 주소는 두 가지 문제를 낸다.
    사이트의 콘텐츠 보안 정책은 리전 주소만 허용하므로 브라우저가 업로드를 막는다. 그리고 새로 만든 버킷에는
    S3가 리전 주소로 리다이렉트할 수 있는데, 이 리다이렉트는 CORS 사전 요청을 깨뜨린다.
    """
    from botocore.config import Config

    region = region or os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
    if not region:
        raise ConfigError("AWS_REGION이 설정되지 않았습니다.")
    return {
        "region_name": region,
        "endpoint_url": f"https://s3.{region}.amazonaws.com",
        "config": Config(signature_version="s3v4", s3={"addressing_style": "virtual"}),
    }


def get_client(name):
    if name not in _clients:
        import boto3

        if name == "dynamodb":
            _clients[name] = boto3.resource("dynamodb")
        elif name == "s3":
            _clients[name] = boto3.client("s3", **s3_client_kwargs())
        else:
            _clients[name] = boto3.client(name)
    return _clients[name]


def get_table(env_name):
    return get_client("dynamodb").Table(os.environ[env_name])


def reset_caches():
    _clients.clear()
    _secret_cache.clear()


# ---- 비밀 값 ----

def _validate_secret(data):
    if not isinstance(data, dict):
        raise ConfigError("인증 비밀 값이 JSON 객체가 아닙니다.")
    for field in SECRET_FIELDS:
        if not isinstance(data.get(field), str) or not data[field].strip():
            raise ConfigError(f"인증 비밀 값에 {field}가 없습니다. Secrets Manager 값을 채워 주세요.")
        if data[field].strip().lower() in _PLACEHOLDER_KEYS or "REPLACE_ME" in data[field].upper():
            raise ConfigError(f"인증 비밀 값의 {field}가 자리 표시자입니다. Secrets Manager 값을 채워 주세요.")
    key = data["sessionSigningKey"]
    if len(key) < MIN_SIGNING_KEY_LENGTH or key.strip().lower() in _PLACEHOLDER_KEYS or "REPLACE_ME" in key.upper():
        raise ConfigError(f"sessionSigningKey가 비어 있거나 자리 표시자이거나 {MIN_SIGNING_KEY_LENGTH}자보다 짧습니다.")
    return data


def load_secret():
    """AUTH_SECRET_ARN의 JSON 비밀을 읽고 검증한다. TTL 동안만 캐시하며, 검증에 실패한 값은 캐시하지 않는다."""
    arn = os.environ["AUTH_SECRET_ARN"]
    cached = _secret_cache.get(arn)
    if cached and now() - cached[0] < SECRET_TTL_SECONDS:
        return cached[1]
    try:
        raw = get_client("secretsmanager").get_secret_value(SecretId=arn)["SecretString"]
        data = json.loads(raw)
    except ConfigError:
        raise
    except Exception:
        raise ConfigError("인증 비밀 값을 읽지 못했습니다.")
    _validate_secret(data)
    _secret_cache[arn] = (now(), data)
    return data


def _site_origin():
    return (os.environ.get("SITE_URL") or "").strip().rstrip("/")


def check_post_request(event, needs_json_body=False):
    """상태를 바꾸는 POST 요청의 교차 출처 방어. 위반하면 (상태 코드, 메시지)를 돌려준다.

    SameSite=Lax 쿠키만으로도 다른 사이트의 POST에는 쿠키가 붙지 않는다. 같은 사이트의 다른 하위 도메인에서
    오는 요청까지 막으려고 Origin 헤더가 있으면 SITE_URL과 같을 때만 통과시킨다.
    """
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    origin = headers.get("origin")
    site = _site_origin()
    if origin is not None and site and origin.rstrip("/") != site:
        return 403, "허용되지 않은 출처의 요청입니다."
    if needs_json_body and event.get("body") is not None:
        ctype = str(headers.get("content-type") or "").split(";")[0].strip().lower()
        if ctype != "application/json":
            return 415, "Content-Type은 application/json이어야 합니다."
    return None


# ---- 응답 ----

def json_default(o):
    if isinstance(o, Decimal):
        return int(o) if o == o.to_integral_value() else float(o)
    raise TypeError(f"not serialisable: {type(o).__name__}")


def to_plain(obj):
    """DynamoDB의 Decimal을 int 또는 float로 바꾼다."""
    if isinstance(obj, Decimal):
        return int(obj) if obj == obj.to_integral_value() else float(obj)
    if isinstance(obj, dict):
        return {k: to_plain(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [to_plain(v) for v in obj]
    return obj


def response(status, body=None, headers=None, cookies=None):
    out = {"statusCode": status, "headers": {"Content-Type": "application/json"}}
    if headers:
        out["headers"].update(headers)
    if cookies:
        out["cookies"] = list(cookies)
    if body is not None:
        out["body"] = json.dumps(body, default=json_default, ensure_ascii=False)
    return out


def error(status, message):
    return response(status, {"error": message})


def redirect(location, cookies=None):
    out = {"statusCode": 302, "headers": {"Location": location}}
    if cookies:
        out["cookies"] = list(cookies)
    return out


# ---- 쿠키 ----

def parse_cookies(event):
    """event["cookies"](목록) 또는 Cookie 헤더에서 쿠키 이름-값 사전을 만든다."""
    raw = list(event.get("cookies") or [])
    headers = event.get("headers") or {}
    for k, v in headers.items():
        if k.lower() == "cookie" and v:
            raw.append(v)
    out = {}
    for chunk in raw:
        for part in str(chunk).split(";"):
            name, sep, value = part.strip().partition("=")
            if sep and name and name not in out:
                out[name] = value
    return out


def make_cookie(name, value, max_age):
    return f"{name}={value}; Max-Age={int(max_age)}; Path=/; HttpOnly; Secure; SameSite=Lax"


def clear_cookie(name):
    return make_cookie(name, "", 0)


# ---- HS256 JWT ----

def _b64e(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64d(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _signature(signing_input, key):
    return hmac.new(key.encode(), signing_input.encode(), hashlib.sha256).digest()


def jwt_sign(payload, key):
    if not isinstance(key, str) or len(key) < MIN_SIGNING_KEY_LENGTH:
        raise ConfigError("서명 키가 너무 짧습니다.")
    header = _b64e(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    body = _b64e(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{header}.{body}"
    return f"{signing_input}.{_b64e(_signature(signing_input, key))}"


def jwt_verify(token, key):
    """서명, alg, exp를 확인하고 payload를 돌려준다. 실패하면 None."""
    try:
        if not isinstance(key, str) or len(key) < MIN_SIGNING_KEY_LENGTH:
            return None
        header_b64, body_b64, sig_b64 = token.split(".")
        header = json.loads(_b64d(header_b64))
        if not isinstance(header, dict) or header.get("alg") != "HS256":
            return None
        expected = _signature(f"{header_b64}.{body_b64}", key)
        if not hmac.compare_digest(expected, _b64d(sig_b64)):
            return None
        payload = json.loads(_b64d(body_b64))
        if not isinstance(payload, dict):
            return None
        exp = payload.get("exp")
        if isinstance(exp, bool) or not isinstance(exp, (int, float)) or exp <= now():
            return None
        return payload
    except Exception:
        return None
