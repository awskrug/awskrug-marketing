import pytest

from shorts import transcript


def _item(text, start, end):
    return {
        "type": "pronunciation",
        "start_time": str(start),
        "end_time": str(end),
        "alternatives": [{"content": text}],
    }


def _punct(text):
    return {"type": "punctuation", "alternatives": [{"content": text}]}


def test_parse_attaches_punctuation():
    data = {"results": {"items": [_item("안녕하세요", "0.5", "0.9"), _punct(".")]}}
    assert transcript.parse_transcribe(data) == [
        {"start": 0.5, "end": 0.9, "text": "안녕하세요."}
    ]


def test_to_sentences_example():
    words = [
        {"start": 0.5, "end": 0.9, "text": "네."},
        {"start": 1.0, "end": 1.4, "text": "좋습니다"},
        {"start": 1.5, "end": 2.0, "text": "감사합니다."},
    ]
    assert transcript.to_sentences(words) == [
        {"start": 0.5, "end": 0.9, "text": "네."},
        {"start": 1.0, "end": 2.0, "text": "좋습니다 감사합니다."},
    ]


def test_words_between_example():
    words = [
        {"start": 1.0, "end": 2.0, "text": "a"},
        {"start": 3.0, "end": 4.0, "text": "b"},
    ]
    assert transcript.words_between(words, 2.5, 5.0) == [
        {"start": 3.0, "end": 4.0, "text": "b"}
    ]


def test_leading_punctuation_dropped():
    data = {"results": {"items": [_punct(".")]}}
    assert transcript.parse_transcribe(data) == []
    data = {"results": {"items": [_punct(","), _item("네", "1.0", "1.2")]}}
    assert transcript.parse_transcribe(data) == [
        {"start": 1.0, "end": 1.2, "text": "네"}
    ]


def test_gap_splits_sentence():
    words = [
        {"start": 0.0, "end": 1.0, "text": "하나"},
        {"start": 2.5, "end": 3.0, "text": "둘"},
    ]
    assert len(transcript.to_sentences(words)) == 2


def test_empty_items():
    assert transcript.parse_transcribe({"results": {"items": []}}) == []


def test_missing_results_raises():
    with pytest.raises(ValueError):
        transcript.parse_transcribe({})


def test_gap_exactly_max_gap_not_split():
    words = [
        {"start": 0.0, "end": 1.0, "text": "하나"},
        {"start": 2.0, "end": 3.0, "text": "둘"},
    ]
    assert len(transcript.to_sentences(words)) == 1


def test_words_between_boundaries_inclusive():
    words = [{"start": 1.0, "end": 2.0, "text": "a"}]
    assert transcript.words_between(words, 1.0, 2.0) == words
    assert transcript.words_between(words, 1.1, 2.0) == []
    assert transcript.words_between(words, 1.0, 1.9) == []


def test_to_sentences_empty_and_question_mark():
    assert transcript.to_sentences([]) == []
    words = [
        {"start": 0.0, "end": 0.5, "text": "맞나요?"},
        {"start": 0.6, "end": 1.0, "text": "네!"},
    ]
    assert [s["text"] for s in transcript.to_sentences(words)] == ["맞나요?", "네!"]


def test_gap_float_error_not_split():
    # 2.1 - 1.1 == 1.0000000000000002 이지만 간격은 1.0초로 취급한다.
    words = [
        {"start": 0.0, "end": 1.1, "text": "a"},
        {"start": 2.1, "end": 3.0, "text": "b"},
    ]
    assert len(transcript.to_sentences(words)) == 1


def test_non_dict_item_skipped():
    data = {"results": {"items": ["x", None, {"type": "pronunciation", "start_time": "0.1", "end_time": "0.2", "alternatives": [{"content": "a"}]}]}}
    assert transcript.parse_transcribe(data) == [{"start": 0.1, "end": 0.2, "text": "a"}]
