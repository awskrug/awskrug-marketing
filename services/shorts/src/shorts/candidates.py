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
        f"- 구간 길이는 {min_sec}초 이상 {max_sec}초 이하여야 합니다. "
        "구간 길이는 마지막 문장의 끝 시각에서 첫 문장의 시작 시각을 뺀 값입니다.\n"
        "- 구간의 핵심 내용이 앞쪽 문장에 오도록 고르세요. "
        "길이가 상한을 넘으면 뒤쪽 문장부터 잘립니다.\n"
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


def _find_array(text: str) -> list:
    """text에서 후보 목록으로 볼 JSON 배열을 찾는다.

    모델이 배열 앞뒤에 설명을 붙이거나 설명 안에 `[0]` 같은 대괄호를 쓸 수 있다.
    그래서 `[`마다 JSON으로 읽어 보고, 객체가 들어 있거나 비어 있는 첫 배열을 고른다.
    그런 배열이 없으면 처음 읽힌 배열을 쓴다.
    """
    dec = json.JSONDecoder()
    pos, first, last_err = 0, None, None
    while True:
        a = text.find("[", pos)
        if a < 0:
            break
        try:
            data, end = dec.raw_decode(text, a)
        except ValueError as e:
            last_err = e
            pos = a + 1
            continue
        if not data or any(isinstance(x, dict) for x in data):
            return data
        if first is None:
            first = data
        pos = end
    if first is not None:
        return first
    if last_err is not None:
        raise ValueError(f"JSON을 읽을 수 없습니다: {last_err}")
    try:
        json.loads(text)
    except ValueError:
        raise ValueError("응답에 JSON 배열이 없습니다") from None
    raise ValueError("JSON 최상위 값이 배열이 아닙니다")


def parse_response(
    text: str, sentences: list[dict], min_sec: float = 30, max_sec: float = 60
) -> list[dict]:
    data = _find_array(text)
    n = len(sentences)
    out: list[dict] = []
    for it in data:
        if not isinstance(it, dict):
            continue
        s, e = it.get("start_index"), it.get("end_index")
        if not (_is_index(s, n) and _is_index(e, n)) or s > e:
            continue
        start, end = sentences[s]["start"], sentences[e]["end"]
        # 모델은 구간 길이 계산을 자주 틀린다. 상한을 넘으면 버리지 않고
        # 끝 문장부터 줄여 상한에 맞춘다.
        while end - start > max_sec + _EPS and e > s:
            e -= 1
            end = sentences[e]["end"]
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
