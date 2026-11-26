"""shorts 명령줄 인터페이스."""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

from . import aws, candidates, pipeline, render, transcript

DEFAULT_MODEL_ID = "us.anthropic.claude-sonnet-4-6"


class CliError(Exception):
    """사용자에게 한 줄로 알릴 오류."""


def _rect(text: str) -> tuple:
    parts = text.split(",")
    try:
        rect = tuple(int(p) for p in parts)
    except ValueError:
        rect = ()
    if len(rect) != 4:
        raise argparse.ArgumentTypeError(f"영역은 x,y,w,h 정수 4개여야 합니다: {text}")
    return rect


def _read_json(path: str):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        raise CliError(f"파일을 읽을 수 없습니다: {path} ({e})")


def _write_json(path: str, data) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _add_aws_opts(p) -> None:
    p.add_argument("--profile", default=None, help="AWS 프로필(기본: AWS_PROFILE)")
    p.add_argument("--region", default="us-east-1")


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="shorts")
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("transcribe", help="영상을 전사한다")
    p.add_argument("video")
    p.add_argument("--bucket", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--job-name", default=None)
    _add_aws_opts(p)

    p = sub.add_parser("candidates", help="쇼츠 후보 구간을 고른다")
    p.add_argument("transcript")
    p.add_argument("--out", required=True)
    p.add_argument("--count", type=int, default=8)
    _add_aws_opts(p)

    p = sub.add_parser("frame", help="영역 지정용 프레임 한 장을 뽑는다")
    p.add_argument("video")
    p.add_argument("--at", required=True, type=float)
    p.add_argument("--out", required=True)

    p = sub.add_parser("render", help="후보 하나를 쇼츠로 렌더링한다")
    p.add_argument("video")
    p.add_argument("transcript")
    p.add_argument("candidates")
    p.add_argument("--index", required=True, type=int)
    p.add_argument("--out-dir", required=True)
    p.add_argument("--slide", type=_rect)
    p.add_argument("--speaker", type=_rect)
    p.add_argument("--logo")
    p.add_argument("--font-file")
    return ap


def _transcribe(a) -> int:
    job = a.job_name or "shorts-" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    c = aws.make_clients(a.profile, a.region)
    data = aws.transcribe_file(a.video, a.bucket, job, c["s3"], c["transcribe"])
    _write_json(a.out, data)
    return 0


def _candidates(a) -> int:
    words = transcript.parse_transcribe(_read_json(a.transcript))
    sentences = transcript.to_sentences(words)
    model_id = os.environ.get("SHORTS_BEDROCK_MODEL_ID") or DEFAULT_MODEL_ID
    c = aws.make_clients(a.profile, a.region)
    resp = aws.ask_model(candidates.build_prompt(sentences, count=a.count), model_id, c["bedrock"])
    found = candidates.parse_response(resp["text"], sentences)
    _write_json(a.out, {
        "model_id": model_id,
        "usage": {"input_tokens": resp["input_tokens"], "output_tokens": resp["output_tokens"]},
        "candidates": found,
    })
    return 0


def _frame(a) -> int:
    render.run(["ffmpeg", "-y", "-ss", str(a.at), "-i", a.video, "-frames:v", "1", a.out])
    return 0


def _render(a) -> int:
    font = a.font_file or os.environ.get("SHORTS_FONT_FILE")
    if not font or not os.path.isfile(font):
        raise CliError("글꼴 파일이 없습니다. --font-file 또는 SHORTS_FONT_FILE로 지정하세요.")
    words = transcript.parse_transcribe(_read_json(a.transcript))
    cands = _read_json(a.candidates).get("candidates", [])
    if not 0 <= a.index < len(cands):
        raise CliError(f"--index {a.index}가 범위 밖입니다. 후보는 {len(cands)}개입니다.")
    paths = pipeline.render_candidate(
        a.video, words, cands[a.index], a.out_dir, font,
        logo=a.logo, slide=a.slide, speaker=a.speaker)
    for path in paths:
        print(path)
    return 0


_HANDLERS = {"transcribe": _transcribe, "candidates": _candidates,
             "frame": _frame, "render": _render}


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
    except SystemExit as e:
        return 2 if e.code else 0
    try:
        return _HANDLERS[args.command](args)
    except Exception as e:  # 모든 오류를 한 줄로 알린다.
        print(f"오류: {e}".replace("\n", " "), file=sys.stderr)
        return 2
