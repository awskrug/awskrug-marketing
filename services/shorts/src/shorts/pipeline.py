"""후보 한 개를 세 가지 화면 구성으로 렌더링한다."""

import os
import shutil
import tempfile
import unicodedata

from . import layouts, render, subtitles, transcript

ELLIPSIS = "\u2026"
# 자막 하단 여백(ASS MarginV). split 레이아웃은 발표자 상자(y=1060..1920) 아래쪽,
# 우하단 로고(높이 160, 하단에서 40px) 위에 둔다.
SPLIT_MARGIN_V = 230
DEFAULT_MARGIN_V = 420


def _units(text: str) -> float:
    """Width in units: 1.0 per wide character (Hangul, CJK, full-width), 0.55 otherwise."""
    total = 0.0
    for ch in text:
        wide = unicodedata.east_asian_width(ch) in ("W", "F") or unicodedata.name(
            ch, ""
        ).startswith(("HANGUL", "CJK"))
        total += 1.0 if wide else 0.55
    return total


def _hard_split(word: str, max_units: float) -> list[str]:
    chunks, cur = [], ""
    for ch in word:
        if cur and _units(cur + ch) > max_units:
            chunks.append(cur)
            cur = ""
        cur += ch
    if cur:
        chunks.append(cur)
    return chunks


def wrap_title(title: str, max_units: float = 15.0, max_lines: int = 2) -> str:
    """Wrap a title into at most max_lines lines of at most max_units width each."""
    lines: list[str] = []
    cur = ""
    for word in title.split():
        if cur and _units(cur + " " + word) <= max_units:
            cur += " " + word
            continue
        if cur:
            lines.append(cur)
            cur = ""
        if _units(word) > max_units:
            chunks = _hard_split(word, max_units)
            lines.extend(chunks[:-1])
            cur = chunks[-1]
        else:
            cur = word
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        last = lines[max_lines - 1]
        while last and _units(last + ELLIPSIS) > max_units:
            last = last[:-1]
        lines = lines[: max_lines - 1] + [last.rstrip() + ELLIPSIS]
    return "\n".join(lines)


def _clip_name(start: float, layout: str) -> str:
    return f"clip-{start:06.1f}-{layout}.mp4"


def render_candidate(
    src: str,
    words: list[dict],
    candidate: dict,
    out_dir: str,
    font_file: str,
    logo: str | None = None,
    slide: tuple | None = None,
    speaker: tuple | None = None,
    endcard_text: str = "AWSKRUG",
) -> list[str]:
    start, end = float(candidate["start"]), float(candidate["end"])
    title = candidate.get("title", "")
    layout_names = (["split"] if slide and speaker else []) + ["blur", "crop"]

    os.makedirs(out_dir, exist_ok=True)
    info = render.probe(src)
    clip_words = transcript.words_between(words, start, end)
    cues = subtitles.build_cues(clip_words, start)

    tmp = tempfile.mkdtemp(prefix=".tmp-", dir=out_dir)
    outputs: list[str] = []
    try:
        title_file = os.path.join(tmp, "title.txt")
        ass_file = os.path.join(tmp, "sub.ass")
        with open(title_file, "w", encoding="utf-8") as f:
            f.write(wrap_title(title))

        endcard = os.path.join(tmp, "endcard.mp4")
        render.run(render.endcard_command(logo, endcard_text, font_file, endcard))

        for name in layout_names:
            margin_v = SPLIT_MARGIN_V if name == "split" else DEFAULT_MARGIN_V
            with open(ass_file, "w", encoding="utf-8") as f:
                f.write(subtitles.build_ass(cues, margin_v=margin_v))
            base = layouts.build_filter(name, info["width"], info["height"], slide, speaker)
            fc = base + ";" + layouts.decorate(title_file, ass_file, font_file, bool(logo))
            main = os.path.join(tmp, f"main-{name}.mp4")
            render.run(render.clip_command(src, start, end, fc, main, logo))
            final = os.path.join(out_dir, _clip_name(start, name))
            render.run(render.concat_command([main, endcard], final))
            outputs.append(final)
    except BaseException:
        for p in outputs:
            if os.path.exists(p):
                os.remove(p)
        raise
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return outputs
