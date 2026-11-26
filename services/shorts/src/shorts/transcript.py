"""Amazon Transcribe 결과 해석과 문장 분리."""

_SENTENCE_END = (".", "?", "!")
_EPS = 1e-9  # 부동소수점 오차 허용


def parse_transcribe(data: dict) -> list[dict]:
    """Transcribe JSON을 단어 목록으로 바꾼다. 문장부호는 앞 단어에 붙인다."""
    if not isinstance(data, dict) or not isinstance(data.get("results"), dict):
        raise ValueError("Transcribe 결과에 results가 없습니다")
    items = data["results"].get("items")
    if not isinstance(items, list):
        raise ValueError("Transcribe 결과에 items가 없습니다")

    words: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        alternatives = item.get("alternatives") or [{}]
        content = alternatives[0].get("content", "")
        if item.get("type") == "punctuation":
            if words:
                words[-1]["text"] += content
            continue
        if "start_time" not in item or "end_time" not in item:
            continue
        words.append(
            {
                "start": float(item["start_time"]),
                "end": float(item["end_time"]),
                "text": content,
            }
        )
    return words


def to_sentences(words: list[dict], max_gap: float = 1.0) -> list[dict]:
    """단어를 문장으로 묶는다. 문장부호 뒤나 max_gap 초과 간격에서 끊는다."""
    sentences: list[dict] = []
    current: list[dict] = []

    def flush() -> None:
        if current:
            sentences.append(
                {
                    "start": current[0]["start"],
                    "end": current[-1]["end"],
                    "text": " ".join(w["text"] for w in current),
                }
            )
            current.clear()

    for word in words:
        if current and word["start"] - current[-1]["end"] > max_gap + _EPS:
            flush()
        current.append(word)
        if word["text"].endswith(_SENTENCE_END):
            flush()
    flush()
    return sentences


def words_between(words: list[dict], start: float, end: float) -> list[dict]:
    """start <= w.start 이고 w.end <= end 인 단어만 돌려준다."""
    return [w for w in words if start <= w["start"] and w["end"] <= end]
