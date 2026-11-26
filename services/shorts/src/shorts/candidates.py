"""쇼츠 후보 선정 프롬프트 구성과 모델 응답 해석."""

import json

TITLE_MAX = 40
_EPS = 1e-9  # 부동소수점 오차 허용


def build_prompt(
    sentences: list[dict], count: int = 8, min_sec: int = 30, max_sec: int = 60
) -> str:
    lines = [
        f"[{i}] {s['start']}-{s['end']} {s['text']}" for i, s in enumerate(sentences)
    ]
    body = "\n".join(lines)
    return (
        "다음은 밋업 발표 영상의 전사문입니다. 각 줄은 `[번호] 시작-끝 텍스트` 형식이며 "
        "시간 단위는 초입니다.\n\n"
        f"{body}\n\n"
        "이 전사문에서 앞뒤 맥락 없이도 이해되는 짧은 영상(쇼츠)용 구간을 고르세요.\n"
        f"- 구간 길이는 {min_sec}초 이상 {max_sec}초 이하여야 합니다.\n"
        f"- 구간은 최대 {count}개까지 고르고, 서로 겹치지 않게 하세요.\n"
        "- 구간은 문장 번호로 지정합니다. start_index는 첫 문장 번호, "
        "end_index는 마지막 문장 번호(포함)입니다.\n"
        "- title은 40자 이내의 한국어 제목, reason은 이 구간을 고른 이유입니다.\n\n"
        "설명 없이 JSON 배열만 출력하세요. 각 항목의 키는 "
        "start_index, end_index, title, reason 입니다.\n"
    )


def _is_index(v: object, n: int) -> bool:
    # bool은 int의 하위 클래스이므로 type()으로 정확히 비교한다.
    return type(v) is int and 0 <= v < n


def _text(v: object) -> str:
    if isinstance(v, str):
        return v
    return "" if v is None else str(v)


def parse_response(
    text: str, sentences: list[dict], min_sec: float = 30, max_sec: float = 60
) -> list[dict]:
    a, b = text.find("["), text.rfind("]")
    if a < 0 or b < a:
        raise ValueError("응답에 JSON 배열이 없습니다")
    try:
        data = json.loads(text[a : b + 1])
    except ValueError as e:
        raise ValueError(f"JSON을 읽을 수 없습니다: {e}") from e
    if not isinstance(data, list):
        raise ValueError("JSON 최상위 값이 배열이 아닙니다")
    n = len(sentences)
    out: list[dict] = []
    for it in data:
        if not isinstance(it, dict):
            continue
        s, e = it.get("start_index"), it.get("end_index")
        if not (_is_index(s, n) and _is_index(e, n)) or s > e:
            continue
        start, end = sentences[s]["start"], sentences[e]["end"]
        if not (min_sec - _EPS <= end - start <= max_sec + _EPS):
            continue
        if any(start < c["end"] - _EPS and c["start"] < end - _EPS for c in out):
            continue
        out.append(
            {
                "start": start,
                "end": end,
                "title": _text(it.get("title"))[:TITLE_MAX],
                "reason": _text(it.get("reason")),
            }
        )
    return out
