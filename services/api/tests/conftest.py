import pytest

import api_fakes
from marketing_api import common

SECRET = {"githubClientId": "cid", "githubClientSecret": "csecret",
          "sessionSigningKey": "test-signing-key-0123456789-abcdefgh"}

ENV = {
    "MEDIA_BUCKET": "media", "JOBS_TABLE": "Jobs", "USERS_TABLE": "Users",
    "ANALYZE_STATE_MACHINE_ARN": "arn:analyze", "RENDER_STATE_MACHINE_ARN": "arn:render",
    "AUTH_SECRET_ARN": "arn:secret", "GITHUB_ORG": "awskrug", "GITHUB_TEAM": "sns-working-group",
    "SITE_URL": "https://site.example",
}


@pytest.fixture(autouse=True)
def env(monkeypatch):
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    common.reset_caches()
    monkeypatch.setattr(common, "now", lambda: 1_800_000_000.0)
    yield
    common.reset_caches()


@pytest.fixture
def aws():
    """가짜 AWS 클라이언트를 주입한다. API Lambda는 AWS 밖의 생산자를 쓰지 않으므로 항상 가짜다."""
    class Aws:
        pass
    a = Aws()
    a.dynamo, a.s3, a.sfn, a.secrets = (api_fakes.FakeDynamo(), api_fakes.FakeS3(),
                                        api_fakes.FakeSfn(), api_fakes.FakeSecrets(SECRET))
    common.set_client("dynamodb", a.dynamo)
    common.set_client("s3", a.s3)
    common.set_client("stepfunctions", a.sfn)
    common.set_client("secretsmanager", a.secrets)
    return a


def session_event(route, github_id="42", login="octo", body=None, path=None):
    import json
    ev = {"routeKey": route, "pathParameters": path or {},
          "requestContext": {"authorizer": {"lambda": {"githubId": github_id, "login": login}}}}
    if body is not None:
        ev["body"] = json.dumps(body)
        ev["headers"] = {"content-type": "application/json"}
    return ev
