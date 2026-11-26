"""cli 테스트. AWS, 네트워크, FFmpeg는 호출하지 않는다."""
import json

import pytest

from shorts import aws, cli, pipeline, render

WORDS_JSON = {
    "results": {
        "items": [
            {"type": "pronunciation", "start_time": "0.0", "end_time": "40.0",
             "alternatives": [{"content": "안녕하세요"}]},
            {"type": "punctuation", "alternatives": [{"content": "."}]},
        ]
    }
}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv("SHORTS_FONT_FILE", raising=False)
    monkeypatch.delenv("SHORTS_BEDROCK_MODEL_ID", raising=False)


@pytest.fixture
def render_files(tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    tr = tmp_path / "t.json"
    tr.write_text(json.dumps(WORDS_JSON), encoding="utf-8")
    cands = tmp_path / "c.json"
    cands.write_text(json.dumps({"model_id": "m", "usage": {}, "candidates": [
        {"start": 0.0, "end": 40.0, "title": "제목", "reason": "r"},
        {"start": 50.0, "end": 90.0, "title": "둘", "reason": "r"}]}), encoding="utf-8")
    font = tmp_path / "f.ttf"
    font.write_bytes(b"x")
    return video, tr, cands, font, tmp_path / "out"


@pytest.fixture
def calls(monkeypatch):
    rec = []

    def fake(src, words, candidate, out_dir, font_file, logo=None, slide=None,
             speaker=None, endcard_text="AWSKRUG"):
        rec.append({"slide": slide, "speaker": speaker, "logo": logo,
                    "candidate": candidate, "font": font_file})
        return ["out/a.mp4", "out/b.mp4"]

    monkeypatch.setattr(pipeline, "render_candidate", fake)
    return rec


def _render(render_files, *extra):
    video, tr, cands, font, out = render_files
    return cli.main(["render", str(video), str(tr), str(cands), "--index", "0",
                     "--out-dir", str(out), "--font-file", str(font), *extra])


def test_rect_parse(render_files, calls, capsys):
    assert _render(render_files, "--slide", "10,20,300,200", "--speaker", "1,2,3,4") == 0
    assert calls[0]["slide"] == (10, 20, 300, 200)
    assert calls[0]["speaker"] == (1, 2, 3, 4)
    assert capsys.readouterr().out.splitlines() == ["out/a.mp4", "out/b.mp4"]


def test_rect_bad(render_files, calls, capsys):
    assert _render(render_files, "--slide", "1,2,3") == 2
    assert not calls
    assert len(capsys.readouterr().err.strip().splitlines()) >= 1


def test_index_out_of_range(render_files, calls):
    video, tr, cands, font, out = render_files
    code = cli.main(["render", str(video), str(tr), str(cands), "--index", "5",
                     "--out-dir", str(out), "--font-file", str(font)])
    assert code == 2 and not calls


def test_missing_font(render_files, calls):
    video, tr, cands, _font, out = render_files
    assert cli.main(["render", str(video), str(tr), str(cands), "--index", "0",
                     "--out-dir", str(out)]) == 2
    assert cli.main(["render", str(video), str(tr), str(cands), "--index", "0",
                     "--out-dir", str(out), "--font-file", "/nonexistent.ttf"]) == 2
    assert not calls


def test_font_from_env(render_files, calls, monkeypatch):
    video, tr, cands, font, out = render_files
    monkeypatch.setenv("SHORTS_FONT_FILE", str(font))
    assert cli.main(["render", str(video), str(tr), str(cands), "--index", "1",
                     "--out-dir", str(out)]) == 0
    assert calls[0]["font"] == str(font)
    assert calls[0]["candidate"]["title"] == "둘"


@pytest.fixture
def fake_aws(monkeypatch):
    seen = {}
    monkeypatch.setattr(aws, "make_clients",
                        lambda profile, region: seen.update(profile=profile, region=region)
                        or {"s3": 1, "transcribe": 2, "bedrock": 3})

    def ask(prompt, model_id, bedrock, max_tokens=4096):
        seen["model_id"] = model_id
        return {"text": '[{"start_index":0,"end_index":0,"title":"T","reason":"R"}]',
                "input_tokens": 10, "output_tokens": 5}

    monkeypatch.setattr(aws, "ask_model", ask)
    return seen


def test_candidates_writes_usage(render_files, fake_aws, tmp_path):
    _v, tr, *_ = render_files
    out = tmp_path / "cand.json"
    assert cli.main(["candidates", str(tr), "--out", str(out)]) == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["model_id"] == "global.anthropic.claude-sonnet-4-6"
    assert data["usage"] == {"input_tokens": 10, "output_tokens": 5}
    assert data["candidates"][0]["title"] == "T"
    assert fake_aws["region"] == "ap-northeast-2" and fake_aws["profile"] is None


def test_model_env_override(render_files, fake_aws, tmp_path, monkeypatch):
    _v, tr, *_ = render_files
    monkeypatch.setenv("SHORTS_BEDROCK_MODEL_ID", "my-model")
    out = tmp_path / "cand.json"
    assert cli.main(["candidates", str(tr), "--out", str(out)]) == 0
    assert fake_aws["model_id"] == "my-model"
    assert json.loads(out.read_text(encoding="utf-8"))["model_id"] == "my-model"


def test_transcribe_writes_json_utf8(render_files, fake_aws, monkeypatch, tmp_path):
    video, *_ = render_files
    got = {}

    def fake_tf(media_path, bucket, job_name, s3, transcribe, **kw):
        got.update(bucket=bucket, job=job_name)
        return {"text": "한글"}

    monkeypatch.setattr(aws, "transcribe_file", fake_tf)
    out = tmp_path / "tr.json"
    assert cli.main(["transcribe", str(video), "--bucket", "b", "--out", str(out),
                     "--profile", "p", "--region", "ap-northeast-2"]) == 0
    assert got["job"].startswith("shorts-") and got["bucket"] == "b"
    assert fake_aws["profile"] == "p" and fake_aws["region"] == "ap-northeast-2"
    assert "한글" in out.read_text(encoding="utf-8")


def test_frame_runs_ffmpeg(monkeypatch, tmp_path):
    cmds = []
    monkeypatch.setattr(render, "run", lambda cmd: cmds.append(cmd))
    assert cli.main(["frame", "v.mp4", "--at", "12.5", "--out", str(tmp_path / "f.png")]) == 0
    assert cmds[0][0] == "ffmpeg" and "12.5" in cmds[0] and "v.mp4" in cmds[0]


def test_error_returns_2(monkeypatch, tmp_path):
    def boom(cmd):
        raise RuntimeError("ffmpeg failed")

    monkeypatch.setattr(render, "run", boom)
    assert cli.main(["frame", "v.mp4", "--at", "1", "--out", str(tmp_path / "f.png")]) == 2
