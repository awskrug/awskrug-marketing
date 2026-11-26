import os
import shutil
import subprocess

import pytest

from shorts import render

FONT = os.environ.get("SHORTS_FONT_FILE")
needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="ffmpeg not installed"
)


def make_input(path, seconds=3):
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", f"testsrc=s=320x180:r=30:d={seconds}",
            "-f", "lavfi", "-i", f"sine=frequency=440:d={seconds}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path),
        ],
        check=True, capture_output=True,
    )


def test_clip_command_shape():
    cmd = render.clip_command("a.mp4", 1.0, 3.0, "[0:v]null[vout]", "o.mp4")
    assert cmd[:12] == [
        "ffmpeg", "-y", "-ss", "1.0", "-to", "3.0", "-i", "a.mp4",
        "-filter_complex", "[0:v]null[vout]", "-map", "[vout]",
    ]
    assert cmd[12:14] == ["-map", "0:a?"]
    assert cmd[-1] == "o.mp4"
    assert "libx264" in cmd and "+faststart" in cmd and "48000" in cmd


def test_clip_command_logo_is_second_input():
    cmd = render.clip_command("a.mp4", 0, 2, "f", "o.mp4", logo="l.png")
    assert cmd.count("-i") == 2
    assert cmd[cmd.index("-i", cmd.index("a.mp4")) + 1] == "l.png"


def test_end_before_start():
    with pytest.raises(ValueError):
        render.clip_command("a.mp4", 3.0, 1.0, "f", "o.mp4")


def test_end_equals_start():
    with pytest.raises(ValueError):
        render.clip_command("a.mp4", 1.0, 1.0, "f", "o.mp4")


def test_negative_start():
    with pytest.raises(ValueError):
        render.clip_command("a.mp4", -1.0, 1.0, "f", "o.mp4")


def test_concat_empty():
    with pytest.raises(ValueError):
        render.concat_command([], "o.mp4")


def test_concat_command_filter():
    cmd = render.concat_command(["a.mp4", "b.mp4"], "o.mp4")
    assert "[v0][a0][v1][a1]concat=n=2:v=1:a=1[v][a]" in cmd[cmd.index("-filter_complex") + 1]
    assert cmd[-1] == "o.mp4"


def test_endcard_bad_seconds():
    with pytest.raises(ValueError):
        render.endcard_command(None, "x", "/f.ttf", "o.mp4", seconds=0)


def test_endcard_text_goes_to_textfile(tmp_path):
    out = str(tmp_path / "end.mp4")
    cmd = render.endcard_command(None, "It's 3:1 100%", "/f/P.ttf", out, 1.0)
    joined = " ".join(cmd)
    assert "It's" not in joined
    txt = [p for p in os.listdir(tmp_path) if p != "end.mp4"]
    assert len(txt) == 1
    assert (tmp_path / txt[0]).read_text(encoding="utf-8") == "It's 3:1 100%"


def test_run_failure_raises():
    with pytest.raises(RuntimeError):
        render.run(["ffmpeg", "-i", "/nonexistent"])


def test_run_missing_binary_raises():
    with pytest.raises(RuntimeError):
        render.run(["definitely-not-a-binary-xyz"])


@pytest.mark.slow
@needs_ffmpeg
def test_clip_renders(tmp_path):
    src = tmp_path / "in.mp4"
    make_input(src)
    out = tmp_path / "o.mp4"
    render.run(render.clip_command(str(src), 0.5, 2.5, "[0:v]scale=1080:1920[vout]", str(out)))
    info = render.probe(str(out))
    assert (info["width"], info["height"]) == (1080, 1920)
    assert abs(info["duration"] - 2.0) <= 0.2


@pytest.mark.slow
@needs_ffmpeg
@pytest.mark.skipif(not FONT, reason="SHORTS_FONT_FILE not set")
def test_endcard_and_concat(tmp_path):
    src = tmp_path / "in.mp4"
    make_input(src)
    clip = tmp_path / "clip.mp4"
    end = tmp_path / "end.mp4"
    out = tmp_path / "final.mp4"
    render.run(render.clip_command(str(src), 0.5, 2.5, "[0:v]scale=1080:1920[vout]", str(clip)))
    render.run(render.endcard_command(None, "It's 3:1 \"x\" 100%", FONT, str(end), 1.0))
    render.run(render.concat_command([str(clip), str(end)], str(out)))
    info = render.probe(str(out))
    assert (info["width"], info["height"]) == (1080, 1920)
    assert abs(info["duration"] - 3.0) <= 0.3


@pytest.mark.slow
@needs_ffmpeg
@pytest.mark.skipif(not FONT, reason="SHORTS_FONT_FILE not set")
def test_endcard_with_logo(tmp_path):
    logo = tmp_path / "logo.png"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=red:s=200x100", "-frames:v", "1", str(logo)],
        check=True, capture_output=True,
    )
    end = tmp_path / "end.mp4"
    render.run(render.endcard_command(str(logo), "AWSKRUG", FONT, str(end), 1.0))
    info = render.probe(str(end))
    assert (info["width"], info["height"]) == (1080, 1920)


def test_endcard_filter_paths_quoted(tmp_path):
    out = str(tmp_path / "e'nd:1.mp4")
    cmd = render.endcard_command(None, "x", "/f/it's:P.ttf", out, 1.0)
    fc = cmd[cmd.index("-filter_complex") + 1]
    assert "fontfile='/f/it'\\\\\\''s\\:P.ttf'" in fc
    assert "e'\\\\\\''nd\\:1.mp4.txt'" in fc


def test_clip_command_tiny_times_not_exponent():
    cmd = render.clip_command("a.mp4", 0.00001, 2.0, "f", "o.mp4")
    assert "e" not in cmd[cmd.index("-ss") + 1].lower()


@pytest.mark.slow
@needs_ffmpeg
@pytest.mark.skipif(not FONT, reason="SHORTS_FONT_FILE not set")
def test_concat_with_silent_source(tmp_path):
    src = tmp_path / "silent.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=s=320x180:r=30:d=3",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(src)],
        check=True, capture_output=True,
    )
    clip, end, out = tmp_path / "c.mp4", tmp_path / "e'nd.mp4", tmp_path / "f.mp4"
    render.run(render.clip_command(str(src), 0.5, 2.5, "[0:v]scale=1080:1920[vout]", str(clip)))
    render.run(render.endcard_command(None, "x", FONT, str(end), 1.0))
    render.run(render.concat_command([str(clip), str(end)], str(out)))
    assert abs(render.probe(str(out))["duration"] - 3.0) <= 0.3
