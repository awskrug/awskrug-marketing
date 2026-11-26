import json

import pytest

from shorts import candidates

S2 = [
    {"start": 0.0, "end": 20.0, "text": "a"},
    {"start": 20.0, "end": 40.0, "text": "b"},
]
S4 = [
    {"start": 0.0, "end": 20.0, "text": "a"},
    {"start": 20.0, "end": 40.0, "text": "b"},
    {"start": 40.0, "end": 60.0, "text": "c"},
    {"start": 60.0, "end": 80.0, "text": "d"},
]


def item(s, e, title="T", reason="R", **kw):
    d = {"start_index": s, "end_index": e, "title": title, "reason": reason}
    d.update(kw)
    return d


def test_parse_basic():
    text = '[{"start_index":0,"end_index":1,"title":"T","reason":"R"}]'
    assert candidates.parse_response(text, S2) == [
        {"start": 0.0, "end": 40.0, "title": "T", "reason": "R"}
    ]


def test_parse_empty():
    assert candidates.parse_response("[]", []) == []


def test_fenced_json():
    text = "설명입니다\n```json\n" + json.dumps([item(0, 1, "제목", "이유")], ensure_ascii=False) + "\n```\n끝"
    assert candidates.parse_response(text, S2) == [
        {"start": 0.0, "end": 40.0, "title": "제목", "reason": "이유"}
    ]


def test_no_array_raises():
    with pytest.raises(ValueError):
        candidates.parse_response("없음", S2)


def test_invalid_json_raises():
    with pytest.raises(ValueError):
        candidates.parse_response("[not json]", S2)


def test_non_list_json_raises():
    with pytest.raises(ValueError):
        candidates.parse_response("]{}[", S2)


def test_out_of_range_dropped():
    assert candidates.parse_response(json.dumps([item(0, 99)]), S2) == []
    assert candidates.parse_response(json.dumps([item(-1, 1)]), S2) == []


def test_too_short_dropped():
    s = [{"start": 0.0, "end": 10.0, "text": "a"}]
    assert candidates.parse_response(json.dumps([item(0, 0)]), s) == []


def test_too_long_dropped():
    s = [{"start": 0.0, "end": 61.0, "text": "a"}]
    assert candidates.parse_response(json.dumps([item(0, 0)]), s) == []


def test_length_boundaries_kept():
    s = [{"start": 0.0, "end": 30.0, "text": "a"}, {"start": 100.0, "end": 160.0, "text": "b"}]
    out = candidates.parse_response(json.dumps([item(0, 0), item(1, 1)]), s)
    assert len(out) == 2


def test_overlap_dropped():
    s = [
        {"start": 0.0, "end": 40.0, "text": "a"},
        {"start": 20.0, "end": 60.0, "text": "b"},
    ]
    out = candidates.parse_response(json.dumps([item(0, 0, "첫"), item(1, 1, "둘")]), s)
    assert [c["title"] for c in out] == ["첫"]


def test_adjacent_not_overlap():
    out = candidates.parse_response(json.dumps([item(0, 1), item(2, 3)]), S4)
    assert len(out) == 2


def test_bool_index_dropped():
    text = '[{"start_index":true,"end_index":1,"title":"T","reason":"R"}]'
    assert candidates.parse_response(text, S2) == []
    text = '[{"start_index":0,"end_index":true,"title":"T","reason":"R"}]'
    assert candidates.parse_response(text, S2) == []


def test_float_and_string_index_dropped():
    assert candidates.parse_response(json.dumps([item(0.0, 1)]), S2) == []
    assert candidates.parse_response(json.dumps([item("0", 1)]), S2) == []


def test_start_after_end_dropped():
    assert candidates.parse_response(json.dumps([item(1, 0)]), S2) == []


def test_non_dict_item_dropped():
    assert candidates.parse_response(json.dumps([1, "x", None, item(0, 1)]), S2) != []
    assert len(candidates.parse_response(json.dumps([1, "x", None, item(0, 1)]), S2)) == 1


def test_missing_index_dropped():
    assert candidates.parse_response('[{"title":"T"}]', S2) == []


def test_missing_title_reason_default_empty():
    out = candidates.parse_response('[{"start_index":0,"end_index":1}]', S2)
    assert out == [{"start": 0.0, "end": 40.0, "title": "", "reason": ""}]


def test_non_string_title_coerced_safely():
    out = candidates.parse_response(json.dumps([item(0, 1, title=None, reason=5)]), S2)
    assert len(out) == 1
    assert isinstance(out[0]["title"], str) and isinstance(out[0]["reason"], str)


def test_title_truncated():
    out = candidates.parse_response(json.dumps([item(0, 1, "가" * 50)], ensure_ascii=False), S2)
    assert out[0]["title"] == "가" * 40


def test_prompt_lists_sentences():
    p = candidates.build_prompt(S2)
    assert "[0] 0.0-20.0 a" in p
    assert "[1] 20.0-40.0 b" in p


def test_prompt_korean_and_params():
    p = candidates.build_prompt(S2, count=5, min_sec=20, max_sec=45)
    assert "JSON" in p
    assert "start_index" in p and "end_index" in p
    assert "5" in p and "20" in p and "45" in p
    assert any("가" <= ch <= "힣" for ch in p)


def test_length_boundary_float_error_kept():
    # 10.3 - 40.3 계열의 부동소수점 오차로 정확히 30초 구간이 탈락하지 않아야 한다.
    sents = [{"start": 0.1, "end": 0.1 + 30.0 - 4e-15, "text": "a"}]
    r = candidates.parse_response('[{"start_index":0,"end_index":0,"title":"t","reason":"r"}]', sents)
    assert len(r) == 1
