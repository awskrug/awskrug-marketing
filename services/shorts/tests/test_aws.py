import io
import json

import pytest

from shorts import aws


class FakeS3:
    def __init__(self, result=None):
        self.uploads = []
        self.result = result if result is not None else {"results": {"items": []}}
        self.gets = []

    def upload_file(self, Filename, Bucket, Key):
        self.uploads.append((Filename, Bucket, Key))

    def get_object(self, Bucket, Key):
        self.gets.append((Bucket, Key))
        return {"Body": io.BytesIO(json.dumps(self.result).encode("utf-8"))}


class FakeTranscribe:
    def __init__(self, statuses, reason=None):
        self.statuses = list(statuses)
        self.reason = reason
        self.started = []
        self.polls = 0

    def start_transcription_job(self, **kwargs):
        self.started.append(kwargs)
        return {}

    def get_transcription_job(self, TranscriptionJobName):
        self.polls += 1
        status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        job = {"TranscriptionJobName": TranscriptionJobName, "TranscriptionJobStatus": status}
        if status == "FAILED":
            job["FailureReason"] = self.reason
        return {"TranscriptionJob": job}


class FakeBedrock:
    def __init__(self, blocks, usage):
        self.blocks = blocks
        self.usage = usage
        self.calls = []

    def converse(self, **kwargs):
        self.calls.append(kwargs)
        return {"output": {"message": {"content": self.blocks}}, "usage": self.usage}


def usage(i, o, t):
    return {"inputTokens": i, "outputTokens": o, "totalTokens": t}


def test_ask_model_basic():
    fake = FakeBedrock([{"text": "hello"}], usage(10, 5, 15))
    assert aws.ask_model("hi", "m", fake) == {
        "text": "hello",
        "input_tokens": 10,
        "output_tokens": 5,
    }
    call = fake.calls[0]
    assert call["modelId"] == "m"
    assert call["inferenceConfig"]["maxTokens"] == 4096


def test_reasoning_tokens_added():
    fake = FakeBedrock([{"text": "x"}], usage(10, 5, 40))
    assert aws.ask_model("hi", "m", fake)["output_tokens"] == 30


def test_total_smaller_not_subtracted():
    fake = FakeBedrock([{"text": "x"}], usage(10, 5, 3))
    assert aws.ask_model("hi", "m", fake)["output_tokens"] == 5


def test_multiple_text_blocks_joined():
    fake = FakeBedrock([{"text": "a"}, {"toolUse": {}}, {"text": "b"}], usage(1, 1, 2))
    assert aws.ask_model("hi", "m", fake)["text"] == "ab"


def test_failed_job_raises():
    t = FakeTranscribe(["IN_PROGRESS", "FAILED"], reason="bad media")
    with pytest.raises(RuntimeError, match="bad media"):
        aws.transcribe_file("/tmp/a.mp4", "b", "j1", FakeS3(), t, poll_sec=0)


def test_timeout():
    t = FakeTranscribe(["IN_PROGRESS"])
    with pytest.raises(TimeoutError):
        aws.transcribe_file("/tmp/a.mp4", "b", "j1", FakeS3(), t, poll_sec=0, timeout_sec=0)


def test_upload_key():
    s3 = FakeS3()
    t = FakeTranscribe(["COMPLETED"])
    aws.transcribe_file("/tmp/a b.mp4", "bk", "j1", s3, t, poll_sec=0)
    assert s3.uploads == [("/tmp/a b.mp4", "bk", "input/j1/a b.mp4")]


def test_completed_returns_result_and_request_shape():
    result = {"results": {"items": [1]}}
    s3 = FakeS3(result)
    t = FakeTranscribe(["IN_PROGRESS", "IN_PROGRESS", "COMPLETED"])
    out = aws.transcribe_file("/tmp/a.mp4", "bk", "j1", s3, t, poll_sec=0)
    assert out == result
    assert t.polls == 3
    req = t.started[0]
    assert req["TranscriptionJobName"] == "j1"
    assert req["LanguageCode"] == "ko-KR"
    assert req["OutputBucketName"] == "bk"
    assert req["OutputKey"] == "transcripts/j1.json"
    assert req["Media"] == {"MediaFileUri": "s3://bk/input/j1/a.mp4"}
    assert s3.gets == [("bk", "transcripts/j1.json")]


def test_make_clients(monkeypatch):
    import sys
    import types

    calls = {}

    class Session:
        def __init__(self, profile_name=None, region_name=None):
            calls["session"] = (profile_name, region_name)

        def client(self, name):
            return "client:" + name

    fake = types.SimpleNamespace(Session=Session)
    monkeypatch.setitem(sys.modules, "boto3", fake)
    out = aws.make_clients(None, "us-east-1")
    assert calls["session"] == (None, "us-east-1")
    assert out == {
        "s3": "client:s3",
        "transcribe": "client:transcribe",
        "bedrock": "client:bedrock-runtime",
    }
