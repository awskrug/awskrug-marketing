import json
import urllib.parse
from decimal import Decimal

import pytest

from conftest import session_event
from marketing_api import common, jobs

GIB5 = 5 * 1024 ** 3
JID = "a" * 32


def call(route, body=None, path=None, **kw):
    out = jobs.handler(session_event(route, body=body, path=path, **kw), None)
    return out["statusCode"], (json.loads(out["body"]) if out.get("body") else None)


def seed(aws, status="READY", **extra):
    item = {"jobId": JID, "ownerId": "42", "ownerLogin": "octo", "fileName": "v.mp4", "title": "T",
            "sourceKey": f"jobs/{JID}/source/v.mp4", "status": status,
            "createdAt": "2026-11-27T00:00:00Z", "updatedAt": "2026-11-27T00:00:00Z",
            "width": Decimal(1920), "height": Decimal(1080), "duration": Decimal("61.5"),
            "candidates": [{"start": Decimal(1), "end": Decimal(30), "title": "c", "reason": "r"}]}
    item.update(extra)
    aws.dynamo.Table("Jobs").items[JID] = item
    return item


# ---- POST /api/jobs ----

def test_create_job(aws):
    st, b = call("POST /api/jobs", {"fileName": "talk.mp4", "size": 1000, "contentType": "video/mp4", "title": "발표"})
    assert st == 201
    jid = b["jobId"]
    assert len(jid) == 32 and int(jid, 16) >= 0
    assert b["uploadHeaders"] == {"Content-Type": "video/mp4"}
    op, params, exp = aws.s3.presigned[0]
    assert (op, exp) == ("put_object", 3600)
    assert params == {"Bucket": "media", "Key": f"jobs/{jid}/source/talk.mp4", "ContentType": "video/mp4"}
    item = aws.dynamo.Table("Jobs").items[jid]
    assert item["status"] == "UPLOADING" and item["ownerId"] == "42" and item["ownerLogin"] == "octo"
    assert item["title"] == "발표" and item["sourceKey"] == f"jobs/{jid}/source/talk.mp4"
    assert item["createdAt"] == item["updatedAt"] == "2027-01-15T08:00:00Z"


def test_create_title_defaults_to_file_name(aws):
    _, b = call("POST /api/jobs", {"fileName": "talk.mp4", "size": 1, "contentType": "video/mp4"})
    assert aws.dynamo.Table("Jobs").items[b["jobId"]]["title"] == "talk.mp4"


@pytest.mark.parametrize("raw,clean", [
    ("../../etc/passwd", ".._.._etc_passwd"),
    ("a\\b.mp4", "a_b.mp4"),
    ("a\x00b\nc\x1f.mp4", "a_b_c_.mp4"),
    ("..", "_"),
])
def test_file_name_sanitised(aws, raw, clean):
    st, b = call("POST /api/jobs", {"fileName": raw, "size": 1, "contentType": "video/mp4"})
    assert st == 201
    assert aws.s3.presigned[0][1]["Key"] == f"jobs/{b['jobId']}/source/{clean}"
    assert aws.dynamo.Table("Jobs").items[b["jobId"]]["fileName"] == clean


def test_size_limit(aws):
    ok = call("POST /api/jobs", {"fileName": "a.mp4", "size": GIB5, "contentType": "video/mp4"})[0]
    over = call("POST /api/jobs", {"fileName": "a.mp4", "size": GIB5 + 1, "contentType": "video/mp4"})[0]
    assert (ok, over) == (201, 400)


@pytest.mark.parametrize("body", [
    None, [], {}, {"fileName": "", "size": 1, "contentType": "v"},
    {"fileName": 3, "size": 1, "contentType": "v"},
    {"fileName": "a", "size": 0, "contentType": "v"},
    {"fileName": "a", "size": -1, "contentType": "v"},
    {"fileName": "a", "size": True, "contentType": "v"},
    {"fileName": "a", "size": 1.5, "contentType": "v"},
    {"fileName": "a", "size": 1, "contentType": ""},
    {"fileName": "a", "size": 1, "contentType": "v", "title": 5},
])
def test_create_validation(aws, body):
    ev = session_event("POST /api/jobs", body=body)
    if body is None:
        ev["body"] = "{not json"
        ev["headers"] = {"content-type": "application/json"}
    assert jobs.handler(ev, None)["statusCode"] == 400
    assert aws.dynamo.Table("Jobs").items == {}


# ---- start ----

def test_start(aws):
    seed(aws, "UPLOADING")
    aws.s3.objects.add(f"jobs/{JID}/source/v.mp4")
    st, b = call("POST /api/jobs/{jobId}/start", path={"jobId": JID})
    assert (st, b) == (202, {"jobId": JID, "status": "QUEUED"})
    assert aws.sfn.started == [("arn:analyze", {"jobId": JID})]
    assert aws.dynamo.Table("Jobs").items[JID]["status"] == "QUEUED"


def test_start_wrong_status_409(aws):
    seed(aws, "QUEUED")
    aws.s3.objects.add(f"jobs/{JID}/source/v.mp4")
    assert call("POST /api/jobs/{jobId}/start", path={"jobId": JID})[0] == 409
    assert aws.sfn.started == []


def test_start_missing_object_409(aws):
    seed(aws, "UPLOADING")
    assert call("POST /api/jobs/{jobId}/start", path={"jobId": JID})[0] == 409
    assert aws.dynamo.Table("Jobs").items[JID]["status"] == "UPLOADING"
    assert aws.sfn.started == []


def test_start_unknown_404(aws):
    assert call("POST /api/jobs/{jobId}/start", path={"jobId": JID})[0] == 404
    assert call("POST /api/jobs/{jobId}/start", path={"jobId": "../x"})[0] == 404


def test_start_sfn_failure_marks_failed(aws):
    seed(aws, "UPLOADING")
    aws.s3.objects.add(f"jobs/{JID}/source/v.mp4")
    aws.sfn.fail = True
    st, b = call("POST /api/jobs/{jobId}/start", path={"jobId": JID})
    assert st == 500 and "boom" not in json.dumps(b)
    assert aws.dynamo.Table("Jobs").items[JID]["status"] == "FAILED"


# ---- list / get ----

def test_list_sorted_desc(aws):
    t = aws.dynamo.Table("Jobs")
    for i, ts in enumerate(["2026-11-25T00:00:00Z", "2026-11-27T00:00:00Z", "2026-11-26T00:00:00Z"]):
        t.items[str(i)] = {"jobId": str(i), "title": "t", "fileName": "f", "status": "READY",
                           "ownerLogin": "o", "createdAt": ts, "candidates": [1]}
    st, b = call("GET /api/jobs")
    assert [j["jobId"] for j in b["jobs"]] == ["1", "2", "0"]
    assert set(b["jobs"][0]) == {"jobId", "title", "fileName", "status", "ownerLogin", "createdAt"}


def test_get_job_with_urls_and_plain_numbers(aws):
    seed(aws, renders={"r1": {"index": 0, "status": "DONE", "slide": None, "speaker": None,
                              "outputs": [{"layout": "split", "key": "jobs/x/r1/split.mp4"}]}})
    st, b = call("GET /api/jobs/{jobId}", path={"jobId": JID})
    assert st == 200
    assert b["width"] == 1920 and isinstance(b["width"], int) and b["duration"] == 61.5
    assert b["sourceUrl"] == f"https://s3.example/jobs/{JID}/source/v.mp4?op=get_object&exp=3600"
    assert b["renders"]["r1"]["outputs"][0]["url"].startswith("https://s3.example/jobs/x/r1/split.mp4")
    assert {p[0] for p in aws.s3.presigned} == {"get_object"}


def test_get_unknown_404(aws):
    assert call("GET /api/jobs/{jobId}", path={"jobId": JID})[0] == 404


# ---- renders ----

def render(aws, body, path=None):
    return call("POST /api/jobs/{jobId}/renders", body, path or {"jobId": JID})


def test_render_ok(aws):
    seed(aws)
    st, b = render(aws, {"index": 0, "slide": [10, 20, 500, 300], "speaker": [600, 20, 300, 300]})
    assert st == 202
    rid = b["renderId"]
    assert len(rid) == 32
    r = aws.dynamo.Table("Jobs").items[JID]["renders"][rid]
    assert r["status"] == "RENDERING" and r["index"] == 0 and r["slide"] == [10, 20, 500, 300]
    assert r["createdAt"] == "2027-01-15T08:00:00Z"
    assert aws.sfn.started == [("arn:render", {"jobId": JID, "renderId": rid})]


def test_render_does_not_clobber_other_renders(aws):
    seed(aws, renders={"old": {"status": "DONE"}})
    _, b = render(aws, {"index": 0, "slide": None, "speaker": None})
    renders = aws.dynamo.Table("Jobs").items[JID]["renders"]
    assert renders["old"] == {"status": "DONE"} and b["renderId"] in renders
    assert renders[b["renderId"]]["slide"] is None


def test_render_both_null_or_missing_ok(aws):
    seed(aws)
    assert render(aws, {"index": 0})[0] == 202


def test_render_sfn_failure_marks_render_failed(aws):
    seed(aws)
    aws.sfn.fail = True
    st, _ = render(aws, {"index": 0, "slide": None, "speaker": None})
    assert st == 500
    (r,) = aws.dynamo.Table("Jobs").items[JID]["renders"].values()
    assert r["status"] == "FAILED"


def test_render_unknown_404(aws):
    assert render(aws, {"index": 0})[0] == 404


@pytest.mark.parametrize("status", ["UPLOADING", "QUEUED", "FAILED"])
def test_render_not_ready_409(aws, status):
    seed(aws, status)
    assert render(aws, {"index": 0})[0] == 409
    assert aws.sfn.started == []


@pytest.mark.parametrize("body", [
    {"index": 1}, {"index": -1}, {"index": True}, {"index": 0.0}, {"index": "0"}, {},
    {"index": 0, "slide": [0, 0, 10, 10]},
    {"index": 0, "slide": None, "speaker": [0, 0, 10, 10]},
    {"index": 0, "slide": [0, 0, 10], "speaker": [0, 0, 10, 10]},
    {"index": 0, "slide": [0, 0, 10, 10.0], "speaker": [0, 0, 10, 10]},
    {"index": 0, "slide": [0, 0, True, 10], "speaker": [0, 0, 10, 10]},
    {"index": 0, "slide": [0, 0, 0, 10], "speaker": [0, 0, 10, 10]},
    {"index": 0, "slide": [-1, 0, 10, 10], "speaker": [0, 0, 10, 10]},
    {"index": 0, "slide": [1000, 0, 921, 10], "speaker": [0, 0, 10, 10]},
    {"index": 0, "slide": [0, 1000, 10, 81], "speaker": [0, 0, 10, 10]},
    {"index": 0, "slide": "x", "speaker": [0, 0, 10, 10]},
])
def test_render_validation_400(aws, body):
    seed(aws)
    assert render(aws, body)[0] == 400
    assert aws.sfn.started == []
    assert "renders" not in aws.dynamo.Table("Jobs").items[JID]


def test_render_rect_on_boundary_ok(aws):
    seed(aws)
    assert render(aws, {"index": 0, "slide": [0, 0, 1920, 1080], "speaker": [1900, 1060, 20, 20]})[0] == 202


# ---- me / routing / auth context ----

def test_me_from_users_table(aws):
    aws.dynamo.Table("Users").items["42"] = {"githubId": "42", "login": "octo", "name": "Octo Cat",
                                             "avatarUrl": "http://a", "firstLoginAt": "x"}
    st, b = call("GET /api/me")
    assert (st, b) == (200, {"githubId": "42", "login": "octo", "name": "Octo Cat", "avatarUrl": "http://a"})


def test_me_without_users_row(aws):
    st, b = call("GET /api/me")
    assert st == 200 and b["githubId"] == "42" and b["login"] == "octo"


def test_missing_authorizer_context_401(aws):
    ev = {"routeKey": "GET /api/jobs", "requestContext": {}}
    assert jobs.handler(ev, None)["statusCode"] == 401


def test_unknown_route_404(aws):
    assert call("DELETE /api/jobs")[0] == 404


def test_base64_body(aws):
    import base64
    ev = session_event("POST /api/jobs")
    ev["body"] = base64.b64encode(json.dumps({"fileName": "a.mp4", "size": 1, "contentType": "v/x"}).encode()).decode()
    ev["isBase64Encoded"] = True
    ev["headers"] = {"content-type": "application/json"}
    assert jobs.handler(ev, None)["statusCode"] == 201


# ---- 내려받기 이름 ----

def _outputs(aws, job_extra, layout="split"):
    seed(aws, renders={"r1": {"index": 0, "status": "DONE", "slide": None, "speaker": None,
                              "outputs": [{"layout": layout, "key": "jobs/x/r1/x.mp4"}]}}, **job_extra)
    st, b = call("GET /api/jobs/{jobId}", path={"jobId": JID})
    assert st == 200
    disp = [p[1].get("ResponseContentDisposition") for p in aws.s3.presigned if p[1]["Key"] == "jobs/x/r1/x.mp4"]
    assert len(disp) == 1
    return disp[0]


def test_output_url_forces_attachment_and_source_stays_inline(aws):
    disp = _outputs(aws, {"title": "My Talk"})
    assert disp == "attachment; filename=\"My Talk-split.mp4\"; filename*=UTF-8''My%20Talk-split.mp4"
    source = [p for p in aws.s3.presigned if p[1]["Key"].endswith("/source/v.mp4")]
    assert len(source) == 1 and "ResponseContentDisposition" not in source[0][1]


def test_download_name_korean_quotes_newline_cannot_inject(aws):
    disp = _outputs(aws, {"title": '발표 "최종"\r\nSet-Cookie: x=1\n/../a\\b'})
    assert "\r" not in disp and "\n" not in disp
    head, _, ext = disp.partition("; filename*=UTF-8''")
    ascii_name = head[len('attachment; filename="'):-1]
    assert '"' not in ascii_name and "/" not in ascii_name and "\\" not in ascii_name
    assert ascii_name.isascii() and ascii_name.startswith("__ ")
    assert ascii_name.endswith("-split.mp4")
    assert urllib.parse.unquote(ext) == "발표 최종 Set-Cookie: x=1 ..ab-split.mp4"
    assert ext.isascii() and '"' not in ext and "'" not in ext


def test_download_name_falls_back_to_file_name_stem_then_job_id(aws):
    assert "filename=\"v-blur.mp4\"" in _outputs(aws, {"title": "v.mp4"}, layout="blur")
    aws.s3.presigned.clear()
    assert f"filename=\"{JID}-crop.mp4\"" in _outputs(aws, {"title": '"\n"', "fileName": "\n"}, layout="crop")


def test_download_name_unit():
    name = jobs.download_file_name({"jobId": JID, "title": "한글", "fileName": "a.mp4"}, "split")
    assert name == "한글-split.mp4"
    assert jobs.content_disposition(name) == (
        'attachment; filename="__-split.mp4"; filename*=UTF-8\'\'%ED%95%9C%EA%B8%80-split.mp4')


# ---- 보안 회귀 테스트 ----

def _post(route, body=None, path=None, headers=None, raw=None, b64=False):
    ev = session_event(route, body=body, path=path)
    if raw is not None:
        ev["body"] = raw
    ev["headers"] = headers or {"content-type": "application/json"}
    if b64:
        ev["isBase64Encoded"] = True
    out = jobs.handler(ev, None)
    return out["statusCode"], out.get("body", "")


@pytest.mark.parametrize("ctype", ["video/mp4\r\nX-Evil: 1", "text", "a/b c", "x" * 200 + "/y", 5, "/mp4"])
def test_create_rejects_bad_content_type(aws, ctype):
    assert _post("POST /api/jobs", {"fileName": "a.mp4", "size": 1, "contentType": ctype})[0] == 400
    assert not aws.s3.presigned


def test_create_defaults_content_type(aws):
    st, body = _post("POST /api/jobs", {"fileName": "a.mp4", "size": 1})
    assert st == 201 and json.loads(body)["uploadHeaders"] == {"Content-Type": "application/octet-stream"}


def test_create_requires_json_content_type_and_origin(aws):
    b = {"fileName": "a.mp4", "size": 1, "contentType": "video/mp4"}
    assert _post("POST /api/jobs", b, headers={"content-type": "application/x-www-form-urlencoded"})[0] == 415
    assert _post("POST /api/jobs", b, headers={"content-type": "application/json", "origin": "https://evil.example"})[0] == 403
    assert _post("POST /api/jobs", b, headers={"content-type": "application/json", "origin": "https://site.example"})[0] == 201


def test_start_and_render_reject_foreign_origin(aws):
    seed(aws)
    h = {"origin": "https://evil.example", "content-type": "application/json"}
    assert _post("POST /api/jobs/{jobId}/start", path={"jobId": JID}, headers=h)[0] == 403
    assert _post("POST /api/jobs/{jobId}/renders", {"index": 0, "slide": None, "speaker": None},
                 path={"jobId": JID}, headers=h)[0] == 403
    assert not aws.sfn.started


def test_deeply_nested_and_oversized_json_is_400(aws):
    assert _post("POST /api/jobs", raw="[" * 100000 + "]" * 100000)[0] == 400
    assert _post("POST /api/jobs", raw='{"a":"' + "x" * 200000 + '"}')[0] == 400
    assert _post("POST /api/jobs", raw="not json!!", b64=True)[0] == 400
    assert _post("POST /api/jobs", raw="[1,2]")[0] == 400


@pytest.mark.parametrize("jid", ["../x", "A" * 32, "a" * 31, "a" * 33, "a" * 31 + "\n", ""])
def test_job_id_validated_before_aws_calls(aws, jid):
    for route in ("GET /api/jobs/{jobId}", "POST /api/jobs/{jobId}/start", "POST /api/jobs/{jobId}/renders"):
        st, _ = _post(route, {"index": 0}, path={"jobId": jid})
        assert st == 404
    assert aws.dynamo.Table("Jobs").updates == [] and not aws.s3.presigned


def test_identity_ignores_client_headers(aws):
    ev = {"routeKey": "GET /api/me", "headers": {"x-github-id": "1", "x-github-login": "evil"}}
    assert jobs.handler(ev, None)["statusCode"] == 401


def test_render_write_is_conditional_on_ready(aws):
    seed(aws)
    table = aws.dynamo.Table("Jobs")
    real = table.update_item

    def flip_then_update(**kw):  # 검사 뒤 쓰기 전에 다른 요청이 상태를 바꾼 경우
        table.items[JID]["status"] = "FAILED"
        return real(**kw)
    table.update_item = flip_then_update
    st, _ = _post("POST /api/jobs/{jobId}/renders", {"index": 0, "slide": None, "speaker": None}, path={"jobId": JID})
    assert st == 409 and not aws.sfn.started and "renders" not in table.items[JID]


def test_error_bodies_do_not_leak_internals(aws, capsys):
    seed(aws)
    aws.sfn.fail = True
    st, body = _post("POST /api/jobs/{jobId}/start", path={"jobId": JID})
    aws.s3.objects.add(f"jobs/{JID}/source/v.mp4")
    st, body = _post("POST /api/jobs/{jobId}/start", path={"jobId": JID})
    cap = capsys.readouterr()
    assert "arn:" not in body and "Traceback" not in body and "RuntimeError" not in body
    assert "X-Amz" not in cap.out + cap.err and "arn:" not in cap.out + cap.err


# ---- 작업 삭제 ----

def _seed(aws, jid="a" * 32, owner="42", status="FAILED", renders=None):
    item = {"jobId": jid, "ownerId": owner, "ownerLogin": "octo", "status": status,
            "sourceKey": f"jobs/{jid}/source/v.mp4"}
    if renders is not None:
        item["renders"] = renders
    aws.dynamo.Table("Jobs").put_item(Item=item)
    aws.s3.objects.update({f"jobs/{jid}/source/v.mp4", f"jobs/{jid}/renders/r/split.mp4", "jobs/other/source/x.mp4"})
    return jid


def _delete(jid, github_id="42", headers=None):
    ev = session_event("DELETE /api/jobs/{jobId}", github_id=github_id, path={"jobId": jid})
    if headers:
        ev["headers"] = headers
    return jobs.handler(ev, None)


@pytest.mark.parametrize("status", ["UPLOADING", "READY", "FAILED"])
def test_delete_job_removes_item_and_media(aws, status):
    jid = _seed(aws, status=status)
    out = _delete(jid)
    assert out["statusCode"] == 204
    assert jid not in aws.dynamo.Table("Jobs").items
    assert aws.s3.objects == {"jobs/other/source/x.mp4"}


@pytest.mark.parametrize("status", ["QUEUED", "EXTRACTING", "TRANSCRIBING", "SELECTING"])
def test_delete_job_refuses_in_progress(aws, status):
    jid = _seed(aws, status=status)
    assert _delete(jid)["statusCode"] == 409
    assert jid in aws.dynamo.Table("Jobs").items


def test_delete_job_refuses_while_rendering(aws):
    jid = _seed(aws, status="READY", renders={"r": {"status": "RENDERING"}})
    assert _delete(jid)["statusCode"] == 409
    assert jid in aws.dynamo.Table("Jobs").items


def test_delete_job_owner_only(aws):
    jid = _seed(aws)
    assert _delete(jid, github_id="7")["statusCode"] == 403
    assert jid in aws.dynamo.Table("Jobs").items


def test_delete_job_missing_and_bad_id(aws):
    assert _delete("b" * 32)["statusCode"] == 404
    assert _delete("../x")["statusCode"] == 404


def test_delete_job_rejects_cross_origin(aws):
    jid = _seed(aws)
    assert _delete(jid, headers={"origin": "https://evil.example"})["statusCode"] == 403
    assert jid in aws.dynamo.Table("Jobs").items


def test_delete_job_keeps_deletion_when_media_cleanup_fails(aws, monkeypatch):
    jid = _seed(aws)
    monkeypatch.setattr(aws.s3, "list_objects_v2", lambda **kw: (_ for _ in ()).throw(RuntimeError("x")))
    assert _delete(jid)["statusCode"] == 204
    assert jid not in aws.dynamo.Table("Jobs").items
