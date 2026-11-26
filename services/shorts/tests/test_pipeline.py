import os
import shutil
import subprocess

import pytest

from shorts import pipeline, render

FONT = os.environ.get("SHORTS_FONT_FILE")

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed"),
    pytest.mark.skipif(not FONT, reason="SHORTS_FONT_FILE not set"),
]

WORDS = [
    {"start": 0.6, "end": 1.0, "text": "안녕하세요."},
    {"start": 1.2, "end": 1.8, "text": "반갑습니다."},
]


@pytest.fixture
def src(tmp_path):
    path = tmp_path / "in.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=s=320x180:r=30:d=3",
            "-f", "lavfi", "-i", "sine=frequency=440:d=3",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path),
        ],
        check=True, capture_output=True,
    )
    return str(path)


def _cand(title="제목"):
    return {"start": 0.5, "end": 2.5, "title": title, "reason": "r"}


def _render(src, tmp_path, title="제목", **kw):
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    paths = pipeline.render_candidate(src, WORDS, _cand(title), str(out), FONT, **kw)
    return out, paths


def test_three_layouts(src, tmp_path):
    out, paths = _render(src, tmp_path, slide=(0, 0, 160, 90), speaker=(160, 90, 160, 90))
    assert [os.path.basename(p) for p in paths] == [
        "clip-0000.5-split.mp4", "clip-0000.5-blur.mp4", "clip-0000.5-crop.mp4",
    ]
    for p in paths:
        info = render.probe(p)
        assert (info["width"], info["height"]) == (1080, 1920)
        assert info["duration"] == pytest.approx(4.0, abs=0.4)


def test_split_skipped_without_regions(src, tmp_path):
    _, paths = _render(src, tmp_path)
    assert [os.path.basename(p) for p in paths] == [
        "clip-0000.5-blur.mp4", "clip-0000.5-crop.mp4",
    ]
    _, paths = _render(src, tmp_path, slide=(0, 0, 160, 90))
    assert len(paths) == 2


def test_title_with_quote_and_colon(src, tmp_path):
    _, paths = _render(src, tmp_path, title="It's 3:1")
    assert len(paths) == 2 and all(os.path.getsize(p) > 0 for p in paths)


def test_temp_cleaned(src, tmp_path):
    out, paths = _render(src, tmp_path, slide=(0, 0, 160, 90), speaker=(160, 90, 160, 90))
    assert sorted(os.listdir(out)) == sorted(os.path.basename(p) for p in paths)


def test_temp_cleaned_on_failure(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    with pytest.raises(RuntimeError):
        pipeline.render_candidate(str(tmp_path / "missing.mp4"), WORDS, _cand(), str(out), FONT)
    assert os.listdir(out) == []


def test_margin_v_per_layout(src, tmp_path, monkeypatch):
    from shorts import subtitles

    seen = []
    real = subtitles.build_ass

    def spy(cues, *a, **kw):
        seen.append(kw.get("margin_v", 420))
        return real(cues, *a, **kw)

    monkeypatch.setattr(subtitles, "build_ass", spy)
    _render(src, tmp_path, slide=(0, 0, 160, 90), speaker=(160, 90, 160, 90))
    assert seen == [230, 420, 420]  # split, blur, crop
