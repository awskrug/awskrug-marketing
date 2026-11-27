"""Fargate 작업 진입점: `python -m shorts.worker audio|render`.

환경 변수: MEDIA_BUCKET, JOBS_TABLE, SHORTS_FONT_FILE, SHORTS_FONT_NAME(선택, 기본 Pretendard).
실패하면 예외를 그대로 올려 비정상 종료한다. FAILED 기록은 상태 기계가 맡는다.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile

from . import pipeline, render, store, transcript

LOGO_KEY = "assets/logo.png"
_LAYOUT_RE = re.compile(r"-(split|blur|crop)\.mp4$")


def _audio_codec(path: str) -> str | None:
    """첫 오디오 스트림의 코덱 이름. 오디오 스트림이 없으면 None."""
    cmd = ["ffprobe", "-v", "error", "-select_streams", "a:0",
           "-show_entries", "stream=codec_name", "-of", "json", path]
    proc = subprocess.run(cmd, capture_output=True, text=True, errors="replace",
                          stdin=subprocess.DEVNULL)
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.splitlines()[-20:])
        raise RuntimeError(f"ffprobe failed with exit code {proc.returncode}:\n{tail}")
    streams = json.loads(proc.stdout).get("streams") or []
    return streams[0].get("codec_name") if streams else None


def _is_missing(err: Exception) -> bool:
    code = str(getattr(err, "response", {}).get("Error", {}).get("Code", ""))
    return code in ("404", "NoSuchKey", "NotFound")


def _require_job(table, job_id: str) -> dict:
    job = store.get_job(table, job_id)
    if job is None:
        raise RuntimeError(f"작업을 찾을 수 없습니다: {job_id}")
    return job


def run_audio(job_id: str, *, s3, table) -> None:
    bucket = os.environ["MEDIA_BUCKET"]
    store.set_status(table, job_id, "EXTRACTING")
    job = _require_job(table, job_id)
    tmp = tempfile.mkdtemp(prefix="shorts-audio-")
    try:
        src = os.path.join(tmp, "source")
        s3.download_file(bucket, job["sourceKey"], src)
        info = render.probe(src)
        store.set_media_info(table, job_id, info["width"], info["height"], info["duration"])
        codec = _audio_codec(src)
        if codec is None:
            raise RuntimeError("원본 영상에 오디오 스트림이 없습니다")
        out = os.path.join(tmp, "audio.m4a")
        audio_args = ["-c:a", "copy"] if codec == "aac" else ["-c:a", "aac", "-b:a", "128k"]
        render.run(["ffmpeg", "-y", "-i", src, "-map", "0:a:0", "-vn", *audio_args, out])
        s3.upload_file(out, bucket, f"jobs/{job_id}/audio.m4a")
        store.set_status(table, job_id, "TRANSCRIBING")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _rect(value) -> tuple | None:
    return tuple(int(v) for v in value) if value else None


def run_render(job_id: str, render_id: str, *, s3, table) -> None:
    bucket = os.environ["MEDIA_BUCKET"]
    font_file = os.environ["SHORTS_FONT_FILE"]
    font_name = os.environ.get("SHORTS_FONT_NAME") or "Pretendard"
    job = _require_job(table, job_id)
    spec = (job.get("renders") or {}).get(render_id)
    if spec is None:
        raise RuntimeError(f"렌더링 요청을 찾을 수 없습니다: {render_id}")
    candidate = job["candidates"][spec["index"]]

    tmp = tempfile.mkdtemp(prefix="shorts-render-")
    try:
        src = os.path.join(tmp, "source")
        s3.download_file(bucket, job["sourceKey"], src)
        tr = os.path.join(tmp, "transcript.json")
        s3.download_file(bucket, f"jobs/{job_id}/transcript.json", tr)
        with open(tr, encoding="utf-8") as f:
            words = transcript.parse_transcribe(json.load(f))

        logo = os.path.join(tmp, "logo.png")
        try:
            s3.download_file(bucket, LOGO_KEY, logo)
        except Exception as e:
            if not _is_missing(e):
                raise
            logo = None

        out_dir = os.path.join(tmp, "out")
        paths = pipeline.render_candidate(
            src, words, candidate, out_dir, font_file, logo=logo,
            slide=_rect(spec.get("slide")), speaker=_rect(spec.get("speaker")),
            font_name=font_name)

        outputs = []
        for path in paths:
            m = _LAYOUT_RE.search(os.path.basename(path))
            if not m:
                raise RuntimeError(f"레이아웃을 알 수 없는 출력 파일입니다: {path}")
            key = f"jobs/{job_id}/renders/{render_id}/{m.group(1)}.mp4"
            s3.upload_file(path, bucket, key)
            outputs.append({"layout": m.group(1), "key": key})
        store.set_render_done(table, job_id, render_id, outputs)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m shorts.worker")
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("audio", help="원본에서 음성을 뽑아 올린다")
    p.add_argument("--job-id", required=True)
    p = sub.add_parser("render", help="후보 하나를 렌더링해 올린다")
    p.add_argument("--job-id", required=True)
    p.add_argument("--render-id", required=True)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    import boto3

    s3 = boto3.client("s3")
    table = boto3.resource("dynamodb").Table(os.environ["JOBS_TABLE"])
    if args.command == "audio":
        run_audio(args.job_id, s3=s3, table=table)
    else:
        run_render(args.job_id, args.render_id, s3=s3, table=table)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
