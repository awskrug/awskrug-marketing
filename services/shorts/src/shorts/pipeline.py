"""후보 한 개를 세 가지 화면 구성으로 렌더링한다."""

import os
import shutil
import tempfile

from . import layouts, render, subtitles, transcript


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
    ass_text = subtitles.build_ass(cues)

    tmp = tempfile.mkdtemp(prefix=".tmp-", dir=out_dir)
    outputs: list[str] = []
    try:
        title_file = os.path.join(tmp, "title.txt")
        ass_file = os.path.join(tmp, "sub.ass")
        with open(title_file, "w", encoding="utf-8") as f:
            f.write(title)
        with open(ass_file, "w", encoding="utf-8") as f:
            f.write(ass_text)

        endcard = os.path.join(tmp, "endcard.mp4")
        render.run(render.endcard_command(logo, endcard_text, font_file, endcard))

        for name in layout_names:
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
