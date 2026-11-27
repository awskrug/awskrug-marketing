import json
import os
import shutil
import subprocess

import pytest

from shorts import pipeline, render, store, worker
from fakes import FakeS3, FakeTable

TRANSCRIPT = json.dumps({"results": {"items": [
    {"type": "pronunciation", "start_time": "1.0", "end_time": "2.0", "alternatives": [{"content": "안녕"}]}]}}).encode()


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("MEDIA_BUCKET", "bucket")
    monkeypatch.setenv("JOBS_TABLE", "jobs")
    monkeypatch.setenv("SHORTS_FONT_FILE", "/font.ttf")
    monkeypatch.delenv("SHORTS_FONT_NAME", raising=False)


def test_audio_copies_aac(env, monkeypatch):
    s3 = FakeS3({"jobs/j1/source/a.mp4": b"video"})
    table = FakeTable({"j1": {"jobId": "j1", "status": "QUEUED", "sourceKey": "jobs/j1/source/a.mp4"}})
    cmds, seen = [], []
    monkeypatch.setattr(render, "probe", lambda p: {"width": 1920, "height": 1080, "duration": 61.5})
    monkeypatch.setattr(worker, "_audio_codec", lambda p: "aac")

    def fake_run(cmd):
        cmds.append(cmd)
        seen.append(table.items["j1"]["status"])
        open(cmd[-1], "wb").write(b"audio")

    monkeypatch.setattr(render, "run", fake_run)
    worker.run_audio("j1", s3=s3, table=table)
    assert "copy" in cmds[0] and "-vn" in cmds[0]
    assert seen == ["EXTRACTING"]
    assert s3.objects["jobs/j1/audio.m4a"] == b"audio"
    item = table.items["j1"]
    assert item["status"] == "TRANSCRIBING"
    assert (item["width"], item["height"]) == (1920, 1080)


def test_audio_encodes_non_aac(env, monkeypatch):
    s3 = FakeS3({"k": b"v"})
    table = FakeTable({"j1": {"jobId": "j1", "sourceKey": "k"}})
    cmds = []
    monkeypatch.setattr(render, "probe", lambda p: {"width": 1, "height": 2, "duration": 3.0})
    monkeypatch.setattr(worker, "_audio_codec", lambda p: "mp3")
    monkeypatch.setattr(render, "run", lambda c: (cmds.append(c), open(c[-1], "wb").write(b"a")))
    worker.run_audio("j1", s3=s3, table=table)
    assert "copy" not in cmds[0] and "aac" in cmds[0]


def test_audio_without_audio_stream_fails_clearly(env, monkeypatch):
    s3 = FakeS3({"k": b"v"})
    table = FakeTable({"j1": {"jobId": "j1", "sourceKey": "k"}})
    monkeypatch.setattr(render, "probe", lambda p: {"width": 1, "height": 2, "duration": 3.0})
    monkeypatch.setattr(worker, "_audio_codec", lambda p: None)
    with pytest.raises(RuntimeError, match="오디오 스트림"):
        worker.run_audio("j1", s3=s3, table=table)
    assert "jobs/j1/audio.m4a" not in s3.objects


def render_setup(logo):
    objs = {"jobs/j1/source/a.mp4": b"v", "jobs/j1/transcript.json": TRANSCRIPT}
    if logo:
        objs["assets/logo.png"] = b"png"
    s3 = FakeS3(objs)
    table = FakeTable({"j1": {
        "jobId": "j1", "sourceKey": "jobs/j1/source/a.mp4",
        "candidates": [{"start": 0.0, "end": 40.0, "title": "t", "reason": "r"},
                       {"start": 12.5, "end": 50.0, "title": "t2", "reason": "r"}],
        "renders": {"r1": {"index": 1, "slide": [0, 0, 10, 10], "speaker": [1, 2, 3, 4], "status": "RENDERING"},
                    "r2": {"index": 0, "status": "RENDERING"}},
    }})
    return s3, table


@pytest.mark.parametrize("logo", [True, False])
def test_render_uploads_and_records(env, monkeypatch, logo):
    s3, table = render_setup(logo)
    calls = {}

    def fake_render(src, words, cand, out_dir, font, logo=None, slide=None, speaker=None,
                    endcard_text="AWSKRUG", font_name="Pretendard"):
        calls.update(src=src, words=words, cand=cand, font=font, logo=logo, slide=slide,
                     speaker=speaker, font_name=font_name)
        os.makedirs(out_dir)
        paths = []
        for n in ("split", "blur", "crop"):
            p = os.path.join(out_dir, f"clip-012.5-{n}.mp4")
            open(p, "wb").write(n.encode())
            paths.append(p)
        return paths

    monkeypatch.setattr(pipeline, "render_candidate", fake_render)
    worker.run_render("j1", "r1", s3=s3, table=table)
    assert calls["cand"]["title"] == "t2"
    assert calls["slide"] == (0, 0, 10, 10) and calls["speaker"] == (1, 2, 3, 4)
    assert calls["words"][0]["text"] == "안녕"
    assert calls["font"] == "/font.ttf" and calls["font_name"] == "Pretendard"
    assert (calls["logo"] is not None) == logo
    assert not os.path.exists(calls["src"])  # 임시 디렉터리 정리
    for n in ("split", "blur", "crop"):
        assert s3.objects[f"jobs/j1/renders/r1/{n}.mp4"] == n.encode()
    r = table.items["j1"]["renders"]
    assert r["r1"]["status"] == "DONE"
    assert r["r1"]["outputs"] == [{"layout": n, "key": f"jobs/j1/renders/r1/{n}.mp4"}
                                  for n in ("split", "blur", "crop")]
    assert r["r2"]["status"] == "RENDERING"


def test_render_null_rects_and_failure_cleans_up(env, monkeypatch):
    s3, table = render_setup(False)
    table.items["j1"]["renders"]["r1"].update(slide=None, speaker=None)
    seen = {}

    def boom(src, *a, **kw):
        seen.update(src=src, slide=kw.get("slide"), speaker=kw.get("speaker"))
        raise RuntimeError("ffmpeg failed")

    monkeypatch.setattr(pipeline, "render_candidate", boom)
    with pytest.raises(RuntimeError):
        worker.run_render("j1", "r1", s3=s3, table=table)
    assert seen["slide"] is None and seen["speaker"] is None
    assert not os.path.exists(seen["src"])
    assert table.items["j1"]["renders"]["r1"]["status"] == "RENDERING"


def test_render_unknown_render_id(env):
    s3, table = render_setup(False)
    with pytest.raises(RuntimeError, match="nope"):
        worker.run_render("j1", "nope", s3=s3, table=table)


def test_main_requires_args(env):
    with pytest.raises(SystemExit):
        worker.main(["render", "--job-id", "j1"])


@pytest.mark.slow
@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg 없음")
@pytest.mark.skipif(not os.environ.get("SHORTS_FONT_FILE"), reason="SHORTS_FONT_FILE 미지정")
@pytest.mark.parametrize("acodec", ["aac", "mp3"])
def test_audio_real_ffmpeg(env, tmp_path, acodec):
    src = tmp_path / "in.mkv"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=s=320x240:d=1",
                    "-f", "lavfi", "-i", "sine=d=1", "-c:v", "libx264", "-c:a", acodec, str(src)], check=True)
    s3 = FakeS3({"k": src.read_bytes()})
    table = FakeTable({"j1": {"jobId": "j1", "sourceKey": "k"}})
    worker.run_audio("j1", s3=s3, table=table)
    assert len(s3.objects["jobs/j1/audio.m4a"]) > 0
    assert table.items["j1"]["width"] == 320
