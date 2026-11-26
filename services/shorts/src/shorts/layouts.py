"""FFmpeg filter strings for the three vertical layouts and decoration."""

OUT_W, OUT_H = 1080, 1920
BAND_H = 200
BAND_COLOR = "0x232F3E"
PANEL_H = (OUT_H - BAND_H) // 2  # 860
TITLE_LINE_SPACING = 12


def _even(n: int) -> int:
    return n - (n % 2)


def _check_region(name: str, region, src_w: int, src_h: int) -> tuple:
    if region is None:
        raise ValueError(f"split layout needs the {name} region")
    x, y, w, h = (int(v) for v in region)
    if w <= 0 or h <= 0 or x < 0 or y < 0 or x + w > src_w or y + h > src_h:
        raise ValueError(f"{name} region {region} is outside the {src_w}x{src_h} source")
    w2, h2 = _even(w), _even(h)
    if w2 < 2 or h2 < 2:
        raise ValueError(f"{name} region {region} is too small")
    return _even(x), _even(y), w2, h2


def _panel(label: str, out: str, rect: tuple) -> str:
    x, y, w, h = rect
    return (
        f"[{label}]crop={w}:{h}:{x}:{y},"
        f"scale={OUT_W}:{PANEL_H}:force_original_aspect_ratio=decrease:force_divisible_by=2,"
        f"pad={OUT_W}:{PANEL_H}:(ow-iw)/2:(oh-ih)/2:black[{out}]"
    )


def build_filter(layout, src_w, src_h, slide=None, speaker=None) -> str:
    if layout == "crop":
        w = _even((src_h * 9 + 8) // 16)
        h = src_h
        if src_w < 2 or src_h < 2:
            raise ValueError(f"source {src_w}x{src_h} is too small")
        if w > src_w:  # source narrower than 9:16: crop the height instead
            w = _even(src_w)
            h = _even((src_w * 16 + 4) // 9)
        x = _even((src_w - w) // 2)
        y = _even((src_h - h) // 2)
        return f"[0:v]crop={w}:{h}:{x}:{y},scale={OUT_W}:{OUT_H},setsar=1[base]"
    if layout == "blur":
        return (
            "[0:v]split=2[a][b];"
            f"[a]scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase,"
            f"crop={OUT_W}:{OUT_H},boxblur=20:5[bg];"
            # fit inside the frame so sources narrower than 9:16 are not cropped
            f"[b]scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=decrease:force_divisible_by=2[fg];"
            "[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[base]"
        )
    if layout == "split":
        s = _check_region("slide", slide, src_w, src_h)
        p = _check_region("speaker", speaker, src_w, src_h)
        return (
            "[0:v]split=2[s0][p0];"
            + _panel("s0", "sl", s)
            + ";"
            + _panel("p0", "sp", p)
            + ";[sl][sp]vstack=inputs=2,"
            f"pad={OUT_W}:{OUT_H}:0:{BAND_H}:black,setsar=1[base]"
        )
    raise ValueError(f"unknown layout: {layout!r}")


def _esc(path: str) -> str:
    """Quote a path for a filter option value (single-quoted, ':' and '\\' escaped)."""
    p = path.replace("\\", "\\\\").replace(":", "\\:").replace("'", "'\\\\\\''")
    return f"'{p}'"


def decorate(title_file: str, ass_file: str, font_file: str, logo: bool) -> str:
    parts = [
        f"[base]drawbox=0:0:{OUT_W}:{BAND_H}:color={BAND_COLOR}:t=fill,"
        f"drawtext=fontfile={_esc(font_file)}:textfile={_esc(title_file)}:expansion=none:"
        f"fontcolor=white:fontsize=64:text_align=C:line_spacing={TITLE_LINE_SPACING}:"
        f"x=(w-text_w)/2:y=({BAND_H}-text_h)/2,"
        f"ass={_esc(ass_file)}"
    ]
    if logo:
        return (
            parts[0]
            + "[sub];[1:v]scale=160:-2[lg];"
            "[sub][lg]overlay=W-w-40:H-h-40[vout]"
        )
    return parts[0] + "[vout]"
