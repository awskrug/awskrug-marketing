import json

from shorts import handlers
from fakes import FakeBedrock, FakeS3, FakeTable


def transcript(n=14):
    items = []
    for i in range(n):
        items.append({"type": "pronunciation", "start_time": str(i * 5.0), "end_time": str(i * 5.0 + 4.0),
                      "alternatives": [{"content": f"단어{i}"}]})
        items.append({"type": "punctuation", "alternatives": [{"content": "."}]})
    return json.dumps({"results": {"items": items}}).encode()


def test_select_candidates(monkeypatch):
    monkeypatch.setenv("MEDIA_BUCKET", "b")
    monkeypatch.delenv("SHORTS_BEDROCK_MODEL_ID", raising=False)
    s3 = FakeS3({"jobs/j1/transcript.json": transcript()})
    table = FakeTable({"j1": {"jobId": "j1", "status": "TRANSCRIBING"}})
    bedrock = FakeBedrock(json.dumps([{"start_index": 0, "end_index": 9, "title": "제목", "reason": "이유"}]))
    out = handlers.select_candidates({"jobId": "j1"}, None, s3=s3, table=table, bedrock=bedrock)
    assert out == {"jobId": "j1", "count": 1}
    item = table.items["j1"]
    assert item["status"] == "READY"
    assert item["usage"] == {"model_id": "global.anthropic.claude-sonnet-4-6", "input_tokens": 100, "output_tokens": 50}
    assert item["candidates"][0]["title"] == "제목"
    assert bedrock.calls[0]["modelId"] == "global.anthropic.claude-sonnet-4-6"


def test_model_id_from_env(monkeypatch):
    monkeypatch.setenv("MEDIA_BUCKET", "b")
    monkeypatch.setenv("SHORTS_BEDROCK_MODEL_ID", "custom")
    s3 = FakeS3({"jobs/j1/transcript.json": transcript()})
    table = FakeTable({"j1": {"jobId": "j1"}})
    bedrock = FakeBedrock("[]")
    handlers.select_candidates({"jobId": "j1"}, None, s3=s3, table=table, bedrock=bedrock)
    assert bedrock.calls[0]["modelId"] == "custom"
    assert table.items["j1"]["status"] == "READY"


def test_no_ffmpeg_modules_imported():
    import sys
    src = open(handlers.__file__, encoding="utf-8").read()
    assert "import render" not in src and "pipeline" not in src
