from shorts.pipeline import _units, wrap_title


def test_short_unchanged():
    assert wrap_title("짧은 제목") == "짧은 제목"


def test_empty():
    assert wrap_title("") == ""


def test_example_titles_two_lines():
    for t in ["지식베이스 구축 시 검증 가능성이 핵심인 이유", "일주일 만에 동작하는 RAG 만든 현실적 조언"]:
        lines = wrap_title(t).split("\n")
        assert len(lines) == 2
        assert all(_units(line) <= 15.0 for line in lines)
        assert "".join(lines).replace(" ", "") == t.replace(" ", "")


def test_very_long_ends_with_ellipsis():
    out = wrap_title("가나다라마바사 " * 10)
    lines = out.split("\n")
    assert len(lines) == 2
    assert lines[-1].endswith("…")
    assert all(_units(line) <= 15.0 for line in lines)


def test_long_ascii_word_hard_split():
    out = wrap_title("a" * 40)
    lines = out.split("\n")
    assert len(lines) == 2
    assert all(_units(line) <= 15.0 for line in lines)
    assert lines[0] == "a" * 27
