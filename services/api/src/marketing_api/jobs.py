"""`/api/me`와 `/api/jobs*` 경로를 처리하는 Lambda."""

import base64
import json
import os
import re
import unicodedata
import urllib.parse
import uuid

from botocore.exceptions import ClientError

from . import common

MAX_UPLOAD_BYTES = 5 * 1024 ** 3  # 미리 서명한 단일 PUT의 상한
URL_TTL_SECONDS = 3600
MAX_FILE_NAME = 255
MAX_TITLE = 200
MAX_DOWNLOAD_BASE = 100

_JOB_ID = re.compile(r"[0-9a-f]{32}")
MAX_BODY_BYTES = 64 * 1024
_CONTENT_TYPE = re.compile(r"[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]{0,62}/[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]{0,62}")
DEFAULT_CONTENT_TYPE = "application/octet-stream"
_UNSAFE = re.compile(r"[\x00-\x1f\x7f/\\]")


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _jobs():
    return common.get_table("JOBS_TABLE")


def _identity(event):
    try:
        ctx = event["requestContext"]["authorizer"]["lambda"]
        return str(ctx["githubId"]), str(ctx["login"])
    except (KeyError, TypeError):
        raise ApiError(401, "로그인이 필요합니다.")


def _body(event):
    raw = event.get("body")
    if raw is None:
        return None
    if not isinstance(raw, str) or len(raw) > MAX_BODY_BYTES * 2:
        raise ApiError(400, "본문이 올바르지 않거나 너무 큽니다.")
    try:
        if event.get("isBase64Encoded"):
            raw = base64.b64decode(raw, validate=True).decode()
        if len(raw.encode()) > MAX_BODY_BYTES:
            raise ApiError(400, "본문이 너무 큽니다.")
        return json.loads(raw)
    except (ValueError, RecursionError):
        raise ApiError(400, "본문이 올바른 JSON이 아닙니다.")


def _guard_post(event, needs_json_body=False):
    blocked = common.check_post_request(event, needs_json_body)
    if blocked:
        raise ApiError(*blocked)


def _job_id(event):
    jid = (event.get("pathParameters") or {}).get("jobId", "")
    if not _JOB_ID.fullmatch(jid):
        raise ApiError(404, "없는 작업입니다.")
    return jid


def _load_job(jid):
    item = _jobs().get_item(Key={"jobId": jid}).get("Item")
    if not item:
        raise ApiError(404, "없는 작업입니다.")
    return item


def _presign_get(key, download_name=None):
    """download_name이 있으면 응답에 Content-Disposition: attachment를 강제한다."""
    params = {"Bucket": os.environ["MEDIA_BUCKET"], "Key": key}
    if download_name:
        params["ResponseContentDisposition"] = content_disposition(download_name)
    return common.get_client("s3").generate_presigned_url(
        "get_object", Params=params, ExpiresIn=URL_TTL_SECONDS)


def _clean_name_part(text):
    """파일 이름에 넣을 수 있게 정리한다. 제어 문자, 줄 바꿈, 따옴표, 경로 구분자를 없앤다."""
    out = []
    for ch in str(text or ""):
        cat = unicodedata.category(ch)
        if cat[0] == "C" or cat in ("Zl", "Zp", "Zs"):
            out.append(" ")  # 제어 문자와 줄 바꿈은 공백으로 바꿔 낱말이 붙지 않게 한다.
        elif ch not in '"\'`/\\;%':
            out.append(ch)
    return " ".join("".join(out).split()).strip(" .")[:MAX_DOWNLOAD_BASE]


def download_file_name(job, layout):
    """`<작업 제목 또는 원본 이름(확장자 제외) 또는 jobId>-<layout>.mp4`."""
    file_name = job.get("fileName") or ""
    stem = file_name.rsplit(".", 1)[0] if "." in file_name.strip(".") else file_name
    title = job.get("title")
    # 제목을 따로 주지 않으면 작업 생성 때 제목이 파일 이름(확장자 포함)으로 채워진다.
    if not title or title == file_name:
        title = stem
    base = _clean_name_part(title) or _clean_name_part(stem) or str(job.get("jobId", ""))
    return f"{base}-{_clean_name_part(layout) or 'video'}.mp4"


def content_disposition(name):
    """RFC 6266: ASCII 대체 이름과 UTF-8 퍼센트 인코딩 이름을 함께 준다."""
    fallback = "".join(c if 32 < ord(c) < 127 or c == " " else "_" for c in name)
    return (f'attachment; filename="{fallback}"; '
            f"filename*=UTF-8''{urllib.parse.quote(name, safe='')}")


def sanitize_file_name(name):
    clean = _UNSAFE.sub("_", name)[:MAX_FILE_NAME]
    return "_" if clean in ("", ".", "..") else clean


# ---- 핸들러 ----

def get_me(event):
    github_id, login = _identity(event)
    user = common.get_table("USERS_TABLE").get_item(Key={"githubId": github_id}).get("Item") or {}
    return common.response(200, {
        "githubId": github_id, "login": user.get("login", login),
        "name": user.get("name"), "avatarUrl": user.get("avatarUrl"),
    })


def create_job(event):
    github_id, login = _identity(event)
    _guard_post(event, needs_json_body=True)
    body = _body(event)
    if not isinstance(body, dict):
        raise ApiError(400, "본문은 JSON 객체여야 합니다.")
    file_name, size, ctype, title = body.get("fileName"), body.get("size"), body.get("contentType"), body.get("title")
    if not isinstance(file_name, str) or not file_name:
        raise ApiError(400, "fileName이 필요합니다.")
    if not _is_int(size) or size <= 0:
        raise ApiError(400, "size는 1 이상의 정수여야 합니다.")
    if size > MAX_UPLOAD_BYTES:
        raise ApiError(400, "5GB를 넘는 파일은 올릴 수 없습니다.")
    if ctype is None:
        ctype = DEFAULT_CONTENT_TYPE
    if not isinstance(ctype, str) or not _CONTENT_TYPE.fullmatch(ctype):
        raise ApiError(400, "contentType이 올바르지 않습니다. 예: video/mp4")
    if title is not None and (not isinstance(title, str) or len(title) > MAX_TITLE):
        raise ApiError(400, "title은 200자 이하의 문자열이어야 합니다.")

    file_name = sanitize_file_name(file_name)
    jid = uuid.uuid4().hex
    key = f"jobs/{jid}/source/{file_name}"
    stamp = common.utc_iso()
    _jobs().put_item(Item={
        "jobId": jid, "ownerId": github_id, "ownerLogin": login, "fileName": file_name,
        "title": title or file_name, "sourceKey": key, "status": "UPLOADING",
        "createdAt": stamp, "updatedAt": stamp,
    })
    url = common.get_client("s3").generate_presigned_url(
        "put_object", Params={"Bucket": os.environ["MEDIA_BUCKET"], "Key": key, "ContentType": ctype},
        ExpiresIn=URL_TTL_SECONDS)
    return common.response(201, {"jobId": jid, "uploadUrl": url, "uploadHeaders": {"Content-Type": ctype}})


def start_job(event):
    _identity(event)
    _guard_post(event)
    jid = _job_id(event)
    job = _load_job(jid)
    if job.get("status") != "UPLOADING":
        raise ApiError(409, "업로드 중인 작업만 시작할 수 있습니다.")
    try:
        common.get_client("s3").head_object(Bucket=os.environ["MEDIA_BUCKET"], Key=job["sourceKey"])
    except ClientError:
        raise ApiError(409, "원본 파일이 아직 올라오지 않았습니다.")
    try:
        _jobs().update_item(
            Key={"jobId": jid}, UpdateExpression="SET #s = :q, updatedAt = :t",
            ConditionExpression="#s = :u", ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":q": "QUEUED", ":u": "UPLOADING", ":t": common.utc_iso()})
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            raise ApiError(409, "이미 시작된 작업입니다.")
        raise
    try:
        common.get_client("stepfunctions").start_execution(
            stateMachineArn=os.environ["ANALYZE_STATE_MACHINE_ARN"], input=json.dumps({"jobId": jid}))
    except Exception:
        _jobs().update_item(
            Key={"jobId": jid}, UpdateExpression="SET #s = :f, #e = :m, updatedAt = :t",
            ExpressionAttributeNames={"#s": "status", "#e": "error"},
            ExpressionAttributeValues={":f": "FAILED", ":m": "분석을 시작하지 못했습니다.", ":t": common.utc_iso()})
        raise ApiError(500, "분석을 시작하지 못했습니다.")
    return common.response(202, {"jobId": jid, "status": "QUEUED"})


def list_jobs(event):
    _identity(event)
    table = _jobs()
    items, kwargs = [], {}
    while True:
        page = table.scan(**kwargs)
        items.extend(page.get("Items", []))
        if not page.get("LastEvaluatedKey"):
            break
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    items.sort(key=lambda i: i.get("createdAt", ""), reverse=True)
    fields = ("jobId", "title", "fileName", "status", "ownerLogin", "createdAt")
    return common.response(200, {"jobs": [{f: i.get(f) for f in fields} for i in items]})


def get_job(event):
    _identity(event)
    job = common.to_plain(_load_job(_job_id(event)))
    job["sourceUrl"] = _presign_get(job["sourceKey"])
    for render in (job.get("renders") or {}).values():
        for out in render.get("outputs") or []:
            out["url"] = _presign_get(out["key"], download_file_name(job, out.get("layout")))
    return common.response(200, job)


def _rect(value, width, height):
    if (not isinstance(value, list) or len(value) != 4 or not all(_is_int(v) for v in value)):
        raise ApiError(400, "slide와 speaker는 정수 4개 [x, y, w, h]여야 합니다.")
    x, y, w, h = value
    if x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > width or y + h > height:
        raise ApiError(400, "사각형이 영상 범위를 벗어났습니다.")
    return value


def create_render(event):
    _identity(event)
    _guard_post(event, needs_json_body=True)
    jid = _job_id(event)
    job = _load_job(jid)
    if job.get("status") != "READY":
        raise ApiError(409, "분석이 끝난 작업만 렌더링할 수 있습니다.")
    body = _body(event)
    if not isinstance(body, dict):
        raise ApiError(400, "본문은 JSON 객체여야 합니다.")
    index = body.get("index")
    candidates = job.get("candidates") or []
    if not _is_int(index) or not 0 <= index < len(candidates):
        raise ApiError(400, "index가 후보 범위를 벗어났습니다.")
    slide, speaker = body.get("slide"), body.get("speaker")
    if (slide is None) != (speaker is None):
        raise ApiError(400, "slide와 speaker는 둘 다 주거나 둘 다 null이어야 합니다.")
    if slide is not None:
        width, height = job.get("width"), job.get("height")
        if width is None or height is None:
            raise ApiError(409, "영상 크기를 아직 알 수 없습니다.")
        slide, speaker = _rect(slide, int(width), int(height)), _rect(speaker, int(width), int(height))

    rid = uuid.uuid4().hex
    entry = {"index": index, "slide": slide, "speaker": speaker, "status": "RENDERING",
             "createdAt": common.utc_iso()}
    _add_render(jid, rid, entry)
    try:
        common.get_client("stepfunctions").start_execution(
            stateMachineArn=os.environ["RENDER_STATE_MACHINE_ARN"],
            input=json.dumps({"jobId": jid, "renderId": rid}))
    except Exception:
        _jobs().update_item(
            Key={"jobId": jid}, UpdateExpression="SET renders.#r.#s = :f, renders.#r.#e = :m",
            ExpressionAttributeNames={"#r": rid, "#s": "status", "#e": "error"},
            ExpressionAttributeValues={":f": "FAILED", ":m": "렌더링을 시작하지 못했습니다."})
        raise ApiError(500, "렌더링을 시작하지 못했습니다.")
    return common.response(202, {"renderId": rid})


def _is_conditional_failure(exc):
    return isinstance(exc, ClientError) and exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException"


def _add_render(jid, rid, entry):
    """renders 맵이 있으면 키 하나만 추가하고, 없으면 맵을 만든다. 다른 렌더링 항목을 덮어쓰지 않는다."""
    table = _jobs()
    for _ in range(3):
        try:
            table.update_item(
                Key={"jobId": jid}, UpdateExpression="SET renders.#r = :v",
                ConditionExpression="attribute_exists(renders) AND #s = :ready",
                ExpressionAttributeNames={"#r": rid, "#s": "status"},
                ExpressionAttributeValues={":v": entry, ":ready": "READY"})
            return
        except Exception as exc:
            if not _is_conditional_failure(exc):
                raise
        try:
            table.update_item(
                Key={"jobId": jid}, UpdateExpression="SET renders = :m",
                ConditionExpression="attribute_not_exists(renders) AND #s = :ready",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={":m": {rid: entry}, ":ready": "READY"})
            return
        except Exception as exc:
            if not _is_conditional_failure(exc):
                raise
        if _load_job(jid).get("status") != "READY":
            raise ApiError(409, "분석이 끝난 작업만 렌더링할 수 있습니다.")
    raise ApiError(500, "렌더링 항목을 기록하지 못했습니다.")


ROUTES = {
    "GET /api/me": get_me,
    "POST /api/jobs": create_job,
    "POST /api/jobs/{jobId}/start": start_job,
    "GET /api/jobs": list_jobs,
    "GET /api/jobs/{jobId}": get_job,
    "POST /api/jobs/{jobId}/renders": create_render,
}


def handler(event, context):
    route = ROUTES.get(event.get("routeKey"))
    try:
        if route is None:
            raise ApiError(404, "없는 경로입니다.")
        return route(event)
    except ApiError as exc:
        return common.error(exc.status, exc.message)
    except common.ConfigError as exc:
        print(f"config error on {event.get('routeKey')}")
        return common.error(500, str(exc))
    except Exception as exc:
        print(f"unhandled {type(exc).__name__} on {event.get('routeKey')}")
        return common.error(500, "서버 오류가 발생했습니다.")
