"""자막 큐 구성과 ASS 자막 생성."""

_SENTENCE_END = (".", "?", "!")
_MIN_GAP = 0.3
_EPS = 1e-9  # 부동소수점 오차 허용


def build_cues(words: list[dict], clip_start: float, max_chars: int = 16) -> list[dict]:
    groups: list[list[dict]] = []
    cur: list[dict] = []
    cur_len = 0
    for word in words:
        text = word["text"]
        add = len(text) if not cur else cur_len + 1 + len(text)
        if cur and add > max_chars:
            groups.append(cur)
            cur, cur_len = [], 0
            add = len(text)
        cur.append(word)
        cur_len = add
        if text.endswith(_SENTENCE_END):
            groups.append(cur)
            cur, cur_len = [], 0
    if cur:
        groups.append(cur)

    cues = [
        {
            "start": max(0.0, g[0]["start"] - clip_start),
            "end": max(0.0, g[-1]["end"] - clip_start),
            "text": " ".join(x["text"] for x in g),
        }
        for g in groups
    ]
    for cue, nxt in zip(cues, cues[1:]):
        if nxt["start"] - cue["end"] < _MIN_GAP - _EPS:
            cue["end"] = nxt["start"]
    return cues


def _ass_time(sec: float) -> str:
    total_cs = round(max(0.0, sec) * 100)
    h, rem = divmod(total_cs, 360000)
    m, rem = divmod(rem, 6000)
    s, cs = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _escape(text: str) -> str:
    text = text.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    return text.replace("{", "\\{").replace("}", "\\}")


def build_ass(
    cues: list[dict], font: str = "Pretendard", font_size: int = 64, margin_v: int = 420
) -> str:
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "PlayResX: 1080",
        "PlayResY: 1920",
        "WrapStyle: 0",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Default,{font},{font_size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,"
        f"0,0,0,0,100,100,0,0,1,4,0,2,60,60,{margin_v},1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for c in cues:
        lines.append(
            f"Dialogue: 0,{_ass_time(c['start'])},{_ass_time(c['end'])},Default,,0,0,0,,"
            f"{_escape(c['text'])}"
        )
    return "\n".join(lines) + "\n"
