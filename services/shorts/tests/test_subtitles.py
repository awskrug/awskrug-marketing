from shorts import subtitles


def w(start, end, text):
    return {"start": start, "end": end, "text": text}


def test_example_cues():
    words = [w(10.0, 10.4, "안녕하세요."), w(10.5, 11.0, "반갑습니다.")]
    assert subtitles.build_cues(words, 10.0) == [
        {"start": 0.0, "end": 0.5, "text": "안녕하세요."},
        {"start": 0.5, "end": 1.0, "text": "반갑습니다."},
    ]


def test_example_empty_ass():
    out = subtitles.build_ass([])
    assert out.startswith("[Script Info]")
    assert "PlayResX: 1080" in out
    assert "PlayResY: 1920" in out
    assert "Dialogue:" not in out


def test_long_word_own_cue():
    cues = subtitles.build_cues([w(0.0, 1.0, "가" * 20)], 0.0)
    assert len(cues) == 1
    assert cues[0]["text"] == "가" * 20


def test_negative_time_clamped():
    cues = subtitles.build_cues([w(9.8, 10.5, "네")], 10.0)
    assert cues[0]["start"] == 0.0


def test_time_format():
    out = subtitles.build_ass([{"start": 3661.5, "end": 3662.0, "text": "a"}])
    assert "Dialogue: 0,1:01:01.50,1:01:02.00," in out


def test_braces_escaped():
    out = subtitles.build_ass([{"start": 0.0, "end": 1.0, "text": "{a}\nb"}])
    assert "\\{a\\} b" in out


def test_max_chars_boundary():
    sixteen = [w(0.0, 1.0, "a" * 7), w(1.0, 2.0, "b" * 8)]
    seventeen = [w(0.0, 1.0, "a" * 8), w(1.0, 2.0, "b" * 8)]
    assert len(subtitles.build_cues(sixteen, 0.0)) == 1
    assert len(subtitles.build_cues(seventeen, 0.0)) == 2


def test_sentence_end_breaks():
    words = [w(0.0, 1.0, "네."), w(1.0, 2.0, "좋아")]
    assert [c["text"] for c in subtitles.build_cues(words, 0.0)] == ["네.", "좋아"]


def test_large_gap_kept():
    words = [w(0.0, 1.0, "네."), w(2.0, 3.0, "좋아")]
    cues = subtitles.build_cues(words, 0.0)
    assert cues[0]["end"] == 1.0


def test_style_fields():
    out = subtitles.build_ass([], font="F", font_size=50, margin_v=300)
    assert "Style: Default,F,50," in out
    assert ",4," in out and ",300,1" in out


def test_gap_exactly_min_not_extended_despite_float_error():
    words = [
        {"start": 0.0, "end": 1.0, "text": "a."},
        {"start": 1.3 - 1e-12, "end": 2.0, "text": "b."},
    ]
    cues = subtitles.build_cues(words, 0.0)
    assert cues[0]["end"] == 1.0
