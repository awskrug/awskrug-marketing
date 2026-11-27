from decimal import Decimal

from shorts import store
from fakes import FakeTable


def make(job=None):
    base = {"jobId": "j1", "status": "QUEUED", "title": "t", "renders": {"r1": {"index": 0, "status": "RENDERING"}}}
    base.update(job or {})
    return FakeTable({"j1": base})


def test_get_job_converts_decimal():
    t = make({"width": Decimal("1920"), "duration": Decimal("12.5"), "slide": [Decimal("1"), Decimal("2")]})
    job = store.get_job(t, "j1")
    assert job["width"] == 1920 and isinstance(job["width"], int)
    assert job["duration"] == 12.5 and isinstance(job["duration"], float)
    assert job["slide"] == [1, 2]
    assert store.get_job(t, "nope") is None


def test_set_status_keeps_siblings():
    t = make()
    store.set_status(t, "j1", "SELECTING")
    item = t.items["j1"]
    assert item["status"] == "SELECTING" and item["title"] == "t" and "r1" in item["renders"]
    assert "updatedAt" in item


def test_set_error():
    t = make()
    store.set_error(t, "j1", "boom")
    assert t.items["j1"]["status"] == "FAILED" and t.items["j1"]["error"] == "boom"


def test_media_info_floats_become_decimal():
    t = make()
    store.set_media_info(t, "j1", 1920, 1080, 61.25)
    assert t.items["j1"]["duration"] == Decimal("61.25")
    assert t.items["j1"]["width"] == 1920


def test_set_candidates_and_usage():
    t = make()
    cands = [{"start": 1.5, "end": 40.0, "title": "a", "reason": "b"}]
    store.set_candidates(t, "j1", cands, {"model_id": "m", "input_tokens": 3, "output_tokens": 4})
    assert t.items["j1"]["candidates"][0]["start"] == Decimal("1.5")
    assert t.items["j1"]["usage"]["model_id"] == "m"


def test_set_render_done_only_touches_that_render():
    t = make()
    t.items["j1"]["renders"]["r2"] = {"index": 1, "status": "RENDERING"}
    store.set_render_done(t, "j1", "r1", [{"layout": "blur", "key": "k"}])
    r = t.items["j1"]["renders"]
    assert r["r1"]["status"] == "DONE" and r["r1"]["outputs"] == [{"layout": "blur", "key": "k"}]
    assert r["r1"]["index"] == 0
    assert r["r2"] == {"index": 1, "status": "RENDERING"}
