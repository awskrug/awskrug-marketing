"""Jobs 테이블 접근. boto3 Table과 같은 인터페이스의 객체를 주입받는다.

DynamoDB는 float을 받지 않으므로 쓸 때 Decimal로, 읽을 때 int 또는 float으로 바꾼다.
update_item은 SET으로 필요한 속성만 바꾸고, renders는 renderId 하위 경로로만 갱신한다.
"""

from datetime import datetime, timezone
from decimal import Decimal


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def to_db(value):
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: to_db(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_db(v) for v in value]
    return value


def from_db(value):
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {k: from_db(v) for k, v in value.items()}
    if isinstance(value, list):
        return [from_db(v) for v in value]
    return value


def _set(table, job_id: str, assignments: dict, names: dict | None = None) -> None:
    """assignments: {식 경로: 값}. updatedAt은 항상 함께 기록한다."""
    assignments = {**assignments, "#updatedAt": _now()}
    names = {**(names or {}), "#updatedAt": "updatedAt"}
    parts, values = [], {}
    for i, (path, val) in enumerate(assignments.items()):
        parts.append(f"{path} = :v{i}")
        values[f":v{i}"] = to_db(val)
    table.update_item(
        Key={"jobId": job_id},
        UpdateExpression="SET " + ", ".join(parts),
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=values,
    )


def get_job(table, job_id: str) -> dict | None:
    item = table.get_item(Key={"jobId": job_id}).get("Item")
    return from_db(item) if item else None


def set_status(table, job_id: str, status: str) -> None:
    _set(table, job_id, {"#status": status}, {"#status": "status"})


def set_error(table, job_id: str, error: str) -> None:
    _set(table, job_id, {"#status": "FAILED", "#error": error},
         {"#status": "status", "#error": "error"})


def set_media_info(table, job_id: str, width: int, height: int, duration: float) -> None:
    _set(table, job_id, {"#w": int(width), "#h": int(height), "#d": float(duration)},
         {"#w": "width", "#h": "height", "#d": "duration"})


def set_candidates(table, job_id: str, candidates: list[dict], usage: dict) -> None:
    _set(table, job_id, {"#c": candidates, "#u": usage}, {"#c": "candidates", "#u": "usage"})


def set_render_done(table, job_id: str, render_id: str, outputs: list[dict]) -> None:
    _set(
        table, job_id,
        {"renders.#r.#s": "DONE", "renders.#r.#o": outputs},
        {"#r": render_id, "#s": "status", "#o": "outputs"},
    )


def set_render_failed(table, job_id: str, render_id: str, error: str) -> None:
    _set(
        table, job_id,
        {"renders.#r.#s": "FAILED", "renders.#r.#e": error},
        {"#r": render_id, "#s": "status", "#e": "error"},
    )
