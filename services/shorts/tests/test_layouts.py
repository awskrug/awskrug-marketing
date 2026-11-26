import pytest

from shorts import layouts


def test_crop_example():
    assert (
        layouts.build_filter("crop", 1920, 1080)
        == "[0:v]crop=608:1080:656:0,scale=1080:1920,setsar=1[base]"
    )


def test_decorate_example():
    f = layouts.decorate("/t/title.txt", "/t/sub.ass", "/f/P.ttf", False)
    assert f.startswith("[base]")
    assert f.endswith("[vout]")
    assert "drawbox" in f and "drawtext" in f and "ass=" in f


def test_unknown_layout():
    with pytest.raises(ValueError):
        layouts.build_filter("x", 1920, 1080)


def test_split_requires_regions():
    with pytest.raises(ValueError):
        layouts.build_filter("split", 1920, 1080)
    with pytest.raises(ValueError):
        layouts.build_filter("split", 1920, 1080, slide=(0, 0, 100, 100))


def test_region_out_of_bounds():
    with pytest.raises(ValueError):
        layouts.build_filter(
            "split", 1920, 1080, slide=(1800, 0, 400, 300), speaker=(0, 0, 100, 100)
        )


def test_region_nonpositive_size():
    with pytest.raises(ValueError):
        layouts.build_filter(
            "split", 1920, 1080, slide=(0, 0, 0, 100), speaker=(0, 0, 100, 100)
        )


def test_crop_odd_width():
    assert "crop=404:720:" in layouts.build_filter("crop", 1280, 720)


def test_path_colon_escaped():
    f = layouts.decorate("/t/title.txt", "C:/a.ass", "/f/P.ttf", False)
    assert "C\\:/a.ass" in f


def test_path_quote_and_backslash_escaped():
    f = layouts.decorate("/t/it's.txt", "/t/a\\b.ass", "/f/P.ttf", False)
    assert "it's" not in f.replace("'\\\\\\''", "")
    assert "a\\\\b.ass" in f


def test_logo_overlay():
    f = layouts.decorate("/t/title.txt", "/t/sub.ass", "/f/P.ttf", True)
    assert "[1:v]" in f and "overlay" in f
    assert f.endswith("[vout]")


def test_blur_and_split_shape():
    b = layouts.build_filter("blur", 1920, 1080)
    assert b.startswith("[0:v]") and b.endswith("[base]") and "boxblur=20:5" in b
    s = layouts.build_filter(
        "split", 1920, 1080, slide=(0, 0, 961, 541), speaker=(960, 540, 960, 540)
    )
    assert s.startswith("[0:v]") and s.endswith("[base]")
    assert "crop=960:540:0:0" in s  # odd region size rounded down to even


def test_decorate_title_expansion_disabled():
    f = layouts.decorate("/t/title.txt", "/t/sub.ass", "/f/P.ttf", False)
    assert "expansion=none" in f


def test_blur_fits_foreground_inside_frame():
    b = layouts.build_filter("blur", 1080, 2400)
    assert "[b]scale=1080:1920:force_original_aspect_ratio=decrease" in b


def test_crop_tiny_source_rejected():
    with pytest.raises(ValueError):
        layouts.build_filter("crop", 1, 1)


def test_crop_portrait_and_narrow_sources():
    assert layouts.build_filter("crop", 1080, 1920).startswith("[0:v]crop=1080:1920:0:0,")
    f = layouts.build_filter("crop", 540, 1920)  # narrower than 9:16
    assert "crop=540:960:0:480," in f


def test_decorate_title_centred_multiline():
    f = layouts.decorate("/t/title.txt", "/t/sub.ass", "/f/P.ttf", False)
    assert "text_align=C" in f
    assert "x=(w-text_w)/2" in f
    assert f"y=({layouts.BAND_H}-text_h)/2" in f
    assert "line_spacing=" in f
