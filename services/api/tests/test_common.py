import base64
import json

from marketing_api import authorizer, common

KEY = "test-signing-key-0123456789-abcdefgh"
NOW = 1_800_000_000


def _b64(d):
    return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()


def test_jwt_roundtrip():
    tok = common.jwt_sign({"sub": "1", "login": "a", "exp": NOW + 10}, KEY)
    assert common.jwt_verify(tok, KEY)["login"] == "a"


def test_jwt_expired_rejected():
    assert common.jwt_verify(common.jwt_sign({"sub": "1", "exp": NOW - 1}, KEY), KEY) is None
    assert common.jwt_verify(common.jwt_sign({"sub": "1", "exp": NOW}, KEY), KEY) is None


def test_jwt_missing_or_bool_exp_rejected():
    assert common.jwt_verify(common.jwt_sign({"sub": "1"}, KEY), KEY) is None
    assert common.jwt_verify(common.jwt_sign({"sub": "1", "exp": True}, KEY), KEY) is None


def test_jwt_tampered_and_wrong_key_rejected():
    tok = common.jwt_sign({"sub": "1", "exp": NOW + 10}, KEY)
    h, b, s = tok.split(".")
    forged = f"{h}.{_b64({'sub': '2', 'exp': NOW + 10})}.{s}"
    assert common.jwt_verify(forged, KEY) is None
    assert common.jwt_verify(tok, "other-key-0123456789-0123456789-xyz") is None


def test_jwt_alg_none_rejected():
    tok = f"{_b64({'alg': 'none', 'typ': 'JWT'})}.{_b64({'sub': '1', 'exp': NOW + 10})}."
    assert common.jwt_verify(tok, KEY) is None


def test_jwt_other_alg_with_valid_hs256_signature_rejected():
    h = _b64({"alg": "HS512", "typ": "JWT"})
    b = _b64({"sub": "1", "exp": NOW + 10})
    sig = base64.urlsafe_b64encode(common._signature(f"{h}.{b}", KEY)).rstrip(b"=").decode()
    assert common.jwt_verify(f"{h}.{b}.{sig}", KEY) is None


def test_jwt_garbage_rejected():
    for t in ["", "a.b", "a.b.c.d", "!!!.???.***"]:
        assert common.jwt_verify(t, KEY) is None


def test_parse_cookies_from_list_and_header():
    assert common.parse_cookies({"cookies": ["a=1", "session=tok"]}) == {"a": "1", "session": "tok"}
    assert common.parse_cookies({"headers": {"Cookie": "a=1; b=2"}}) == {"a": "1", "b": "2"}
    assert common.parse_cookies({}) == {}


def test_make_cookie_attributes():
    c = common.make_cookie("session", "v", 60)
    for part in ["session=v", "Max-Age=60", "Path=/", "HttpOnly", "Secure", "SameSite=Lax"]:
        assert part in c
    assert "Max-Age=0" in common.clear_cookie("session")


def test_to_plain_decimal():
    from decimal import Decimal
    assert common.to_plain({"a": Decimal("3"), "b": [Decimal("1.5")]}) == {"a": 3, "b": [1.5]}
    assert isinstance(common.to_plain(Decimal("3")), int)


def test_secret_cached(aws):
    common.load_secret()
    common.load_secret()
    assert aws.secrets.calls == 1


def _ev(aws, claims):
    tok = common.jwt_sign(claims, KEY)
    return {"cookies": [f"session={tok}"]}


def test_authorizer_accepts_valid(aws):
    out = authorizer.handler(_ev(aws, {"sub": "42", "login": "octo", "exp": NOW + 5}), None)
    assert out == {"isAuthorized": True, "context": {"githubId": "42", "login": "octo"}}


def test_authorizer_denies(aws):
    assert authorizer.handler({}, None) == {"isAuthorized": False}
    assert authorizer.handler(_ev(aws, {"sub": "42", "login": "o", "exp": NOW - 5}), None) == {"isAuthorized": False}
    assert authorizer.handler({"headers": {"cookie": "session=junk"}}, None) == {"isAuthorized": False}


# ---- 보안 회귀 테스트 ----

import pytest


@pytest.mark.parametrize("key", ["", "REPLACE_ME", "short", None, 123])
def test_jwt_rejects_weak_or_bad_keys(key):
    good = common.jwt_sign({"sub": "1", "exp": NOW + 10}, KEY)
    assert common.jwt_verify(good, key) is None


def test_jwt_sign_refuses_weak_key():
    with pytest.raises(common.ConfigError):
        common.jwt_sign({"exp": NOW + 10}, "REPLACE_ME")


def test_jwt_missing_alg_and_non_dict_payload_rejected():
    h = _b64({"typ": "JWT"})
    b = _b64({"sub": "1", "exp": NOW + 10})
    sig = base64.urlsafe_b64encode(common._signature(f"{h}.{b}", KEY)).rstrip(b"=").decode()
    assert common.jwt_verify(f"{h}.{b}.{sig}", KEY) is None
    h = _b64({"alg": "HS256"})
    b = _b64([1, 2])
    sig = base64.urlsafe_b64encode(common._signature(f"{h}.{b}", KEY)).rstrip(b"=").decode()
    assert common.jwt_verify(f"{h}.{b}.{sig}", KEY) is None
    assert common.jwt_verify(None, KEY) is None


@pytest.mark.parametrize("secret", [
    {"githubClientId": "a", "githubClientSecret": "b", "sessionSigningKey": "REPLACE_ME"},
    {"githubClientId": "a", "githubClientSecret": "b", "sessionSigningKey": ""},
    {"githubClientId": "a", "githubClientSecret": "b"},
    {"githubClientSecret": "b", "sessionSigningKey": "x" * 40},
    {"githubClientId": "", "githubClientSecret": "b", "sessionSigningKey": "x" * 40},
    # 배포 직후의 비밀 값: GitHub 값 두 개가 자리 표시자로 남아 있다.
    {"githubClientId": "REPLACE_ME", "githubClientSecret": "REPLACE_ME", "sessionSigningKey": "x" * 48},
    {"githubClientId": "a", "githubClientSecret": "replace_me", "sessionSigningKey": "x" * 48},
])
def test_load_secret_validates(aws, secret):
    aws.secrets.secret = secret
    with pytest.raises(common.ConfigError):
        common.load_secret()
    aws.secrets.secret = {"githubClientId": "a", "githubClientSecret": "b", "sessionSigningKey": "x" * 40}
    assert common.load_secret()["githubClientId"] == "a"  # 실패한 값은 캐시되지 않는다


def test_secret_refreshed_after_ttl(aws, monkeypatch):
    common.load_secret()
    aws.secrets.secret = dict(aws.secrets.secret, githubClientId="rotated")
    monkeypatch.setattr(common, "now", lambda: NOW + common.SECRET_TTL_SECONDS + 1)
    assert common.load_secret()["githubClientId"] == "rotated"


def test_authorizer_rejects_non_string_claims_and_bad_secret(aws):
    assert authorizer.handler(_ev(aws, {"sub": 42, "login": "o", "exp": NOW + 5}), None) == {"isAuthorized": False}
    assert authorizer.handler(_ev(aws, {"sub": "1", "login": ["o"], "exp": NOW + 5}), None) == {"isAuthorized": False}
    aws.secrets.secret = {"sessionSigningKey": "REPLACE_ME"}
    common.reset_caches(); common.set_client("secretsmanager", aws.secrets)
    assert authorizer.handler(_ev(aws, {"sub": "1", "login": "o", "exp": NOW + 5}), None) == {"isAuthorized": False}


def test_authorizer_reads_cookie_header_any_case_and_does_not_log(aws, capsys):
    tok = common.jwt_sign({"sub": "1", "login": "o", "exp": NOW + 5}, KEY)
    out = authorizer.handler({"headers": {"CoOkIe": f"x=1; session={tok}"}}, None)
    assert out["isAuthorized"] is True
    authorizer.handler({"cookies": ["session=zzz.secretcookie.zzz"]}, None)
    cap = capsys.readouterr()
    assert "secretcookie" not in cap.out + cap.err


def test_check_post_request_origin_and_content_type():
    ok = {"headers": {"Origin": "https://site.example", "Content-Type": "application/json; charset=utf-8"}, "body": "{}"}
    assert common.check_post_request(ok, True) is None
    assert common.check_post_request({"headers": {}}, True) is None  # Origin 없음, 본문 없음
    assert common.check_post_request({"headers": {"origin": "https://evil.example"}})[0] == 403
    assert common.check_post_request({"headers": {"origin": "null"}})[0] == 403
    form = {"headers": {"content-type": "application/x-www-form-urlencoded"}, "body": "a=b"}
    assert common.check_post_request(form, True)[0] == 415
    assert common.check_post_request({"body": "{}"}, True)[0] == 415


def test_presigned_url_uses_regional_host():
    # 실제 boto3로 서명한다(네트워크 호출 없음). 호스트가 CSP와 CORS가 허용하는 리전 주소여야 한다.
    import boto3

    client = boto3.client("s3", aws_access_key_id="AKIDEXAMPLE", aws_secret_access_key="x" * 40,
                          **common.s3_client_kwargs("ap-northeast-2"))
    url = client.generate_presigned_url(
        "put_object", Params={"Bucket": "my-bucket", "Key": "jobs/a/source/한글 이름.mp4", "ContentType": "video/mp4"},
        ExpiresIn=60)
    assert url.startswith("https://my-bucket.s3.ap-northeast-2.amazonaws.com/jobs/a/source/")


def test_s3_client_kwargs_requires_region(monkeypatch):
    monkeypatch.delenv("AWS_REGION", raising=False)
    monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
    with pytest.raises(common.ConfigError):
        common.s3_client_kwargs()
