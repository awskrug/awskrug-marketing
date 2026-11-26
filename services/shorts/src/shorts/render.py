"""FFmpeg 명령 구성과 실행."""

import json
import os
import subprocess

BG_COLOR = "0x232F3E"

_ENCODE_ARGS = [
    "-r", "30",
    "-c:v", "libx264", "-crf", "20", "-pix_fmt", "yuv420p",
    "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
    "-movflags", "+faststart",
]


def _escape_filter_path(path: str) -> str:
    """필터 옵션 값 안의 경로에서 \\, :, '를 이스케이프한다."""
    p = path.replace("\\", "\\\\").replace(":", "\\:").replace("'", "'\\\\\\''")
    return f"'{p}'"


def _ts(x: float) -> str:
    s = str(float(x))
    return f"{x:.6f}" if "e" in s.lower() else s


def clip_command(
    src: str,
    start: float,
    end: float,
    filter_complex: str,
    out: str,
    logo: str | None = None,
) -> list[str]:
    if start < 0:
        raise ValueError(f"start must be >= 0: {start}")
    if end <= start:
        raise ValueError(f"end must be greater than start: {start}..{end}")
    cmd = ["ffmpeg", "-y", "-ss", _ts(start), "-to", _ts(end), "-i", src]
    if logo:
        cmd += ["-i", logo]
    cmd += [
        "-filter_complex", filter_complex,
        "-map", "[vout]", "-map", "0:a?",
        *_ENCODE_ARGS,
        out,
    ]
    return cmd


def endcard_command(
    logo: str | None,
    text: str,
    font_file: str,
    out: str,
    seconds: float = 2.0,
) -> list[str]:
    if seconds <= 0:
        raise ValueError(f"seconds must be positive: {seconds}")
    # 문구는 따옴표와 콜론 문제를 피하려고 출력 파일 옆 텍스트 파일로 전달한다.
    text_file = out + ".txt"
    with open(text_file, "w", encoding="utf-8") as f:
        f.write(text)
    draw = (
        f"drawtext=fontfile={_escape_filter_path(font_file)}"
        f":textfile={_escape_filter_path(text_file)}:expansion=none"
        ":fontcolor=white:fontsize=64:x=(w-text_w)/2"
    )
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"color=c={BG_COLOR}:s=1080x1920:r=30:d={seconds}",
        "-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo:d={seconds}",
    ]
    if logo:
        cmd += ["-loop", "1", "-t", str(seconds), "-i", logo]
        fc = (
            "[2:v]scale=400:-2[lg];"
            "[0:v][lg]overlay=(W-w)/2:H/2-h-40[bg];"
            f"[bg]{draw}:y=H/2+40[vout]"
        )
    else:
        fc = f"[0:v]{draw}:y=(h-text_h)/2[vout]"
    cmd += [
        "-filter_complex", fc,
        "-map", "[vout]", "-map", "1:a",
        "-t", str(seconds),
        *_ENCODE_ARGS,
        out,
    ]
    return cmd


def _ffprobe_json(path: str, args: list[str]) -> dict | None:
    cmd = ["ffprobe", "-v", "error", *args, "-of", "json", path]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, errors="replace",
                              stdin=subprocess.DEVNULL)
        if proc.returncode != 0:
            return None
        return json.loads(proc.stdout)
    except (OSError, ValueError):
        return None


def _has_audio(path: str) -> bool:
    """오디오 스트림이 없다고 확인된 경우에만 False. 확인할 수 없으면 있다고 본다."""
    data = _ffprobe_json(path, ["-select_streams", "a", "-show_entries", "stream=index"])
    if data is None:
        return True
    return bool(data.get("streams"))


def _duration(path: str) -> float:
    data = _ffprobe_json(path, ["-show_entries", "format=duration"])
    try:
        return float(data["format"]["duration"])
    except (TypeError, KeyError, ValueError):
        raise RuntimeError(f"cannot read duration of {path}")


def concat_command(parts: list[str], out: str) -> list[str]:
    if not parts:
        raise ValueError("parts must not be empty")
    cmd = ["ffmpeg", "-y"]
    for p in parts:
        cmd += ["-i", p]
    n = len(parts)
    # 조각마다 SAR, 프레임레이트, 오디오 형식을 맞춘 뒤 이어 붙인다.
    # 오디오 스트림이 없는 조각에는 같은 길이의 무음을 만들어 붙인다(concat은 모든 조각에 오디오가 필요하다).
    norm = ""
    for i, part in enumerate(parts):
        norm += f"[{i}:v]setsar=1,fps=30,format=yuv420p[v{i}];"
        if _has_audio(part):
            norm += f"[{i}:a]aresample=48000,aformat=channel_layouts=stereo[a{i}];"
        else:
            dur = _duration(part)
            norm += f"anullsrc=r=48000:cl=stereo:d={dur:.3f}[a{i}];"
    labels = "".join(f"[v{i}][a{i}]" for i in range(n))
    fc = f"{norm}{labels}concat=n={n}:v=1:a=1[v][a]"
    cmd += [
        "-filter_complex", fc,
        "-map", "[v]", "-map", "[a]",
        *_ENCODE_ARGS,
        out,
    ]
    return cmd


def run(cmd: list[str]) -> None:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, errors="replace",
                              stdin=subprocess.DEVNULL)
    except OSError as e:
        raise RuntimeError(f"cannot execute {cmd[0]}: {e}") from e
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.splitlines()[-20:])
        raise RuntimeError(f"{cmd[0]} failed with exit code {proc.returncode}:\n{tail}")


def probe(path: str) -> dict:
    cmd = [
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height:format=duration",
        "-of", "json", path,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    except OSError as e:
        raise RuntimeError(f"cannot execute ffprobe: {e}") from e
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.splitlines()[-20:])
        raise RuntimeError(f"ffprobe failed with exit code {proc.returncode}:\n{tail}")
    data = json.loads(proc.stdout)
    streams = data.get("streams") or []
    if not streams:
        raise RuntimeError(f"no video stream in {os.fspath(path)}")
    return {
        "width": int(streams[0]["width"]),
        "height": int(streams[0]["height"]),
        "duration": float(data["format"]["duration"]),
    }
