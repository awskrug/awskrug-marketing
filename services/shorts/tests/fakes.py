"""store/worker/handlers 테스트용 손으로 쓴 가짜 AWS 클라이언트."""
import copy
import io
import json
import re
from decimal import Decimal


class FakeTable:
    """update_item의 SET 식만 해석하는 DynamoDB Table 가짜. float 쓰기는 거부한다."""

    def __init__(self, items=None):
        self.items = {k: dict(v) for k, v in (items or {}).items()}

    def get_item(self, Key, **_):
        item = self.items.get(Key["jobId"])
        return {"Item": copy.deepcopy(item)} if item else {}

    def update_item(self, Key, UpdateExpression, ExpressionAttributeValues=None,
                    ExpressionAttributeNames=None, **_):
        names = ExpressionAttributeNames or {}
        values = ExpressionAttributeValues or {}
        assert UpdateExpression.startswith("SET ")
        item = self.items.setdefault(Key["jobId"], {"jobId": Key["jobId"]})
        for part in UpdateExpression[4:].split(","):
            path, val = [p.strip() for p in part.split("=")]
            v = values[val]
            self._reject_float(v)
            keys = [names.get(k, k) for k in re.split(r"\.", path)]
            cur = item
            for k in keys[:-1]:
                cur = cur[k]  # 중간 맵이 없으면 KeyError: 형제 속성을 덮어쓰지 않는다
            cur[keys[-1]] = v

    def _reject_float(self, v):
        assert not isinstance(v, float), "DynamoDB는 float을 받지 않는다"
        if isinstance(v, dict):
            [self._reject_float(x) for x in v.values()]
        if isinstance(v, list):
            [self._reject_float(x) for x in v]


class NoSuchKey(Exception):
    def __init__(self):
        self.response = {"Error": {"Code": "404"}}


class FakeS3:
    def __init__(self, objects=None):
        self.objects = dict(objects or {})
        self.uploads = []

    def download_file(self, Bucket, Key, Filename):
        if Key not in self.objects:
            from botocore.exceptions import ClientError
            raise ClientError({"Error": {"Code": "404", "Message": "nf"}}, "HeadObject")
        with open(Filename, "wb") as f:
            f.write(self.objects[Key])

    def upload_file(self, Filename, Bucket, Key, **_):
        with open(Filename, "rb") as f:
            self.objects[Key] = f.read()
        self.uploads.append(Key)

    def get_object(self, Bucket, Key):
        return {"Body": io.BytesIO(self.objects[Key])}


class FakeBedrock:
    def __init__(self, text, inp=100, out=50):
        self.text, self.inp, self.out = text, inp, out
        self.calls = []

    def converse(self, **kw):
        self.calls.append(kw)
        return {"output": {"message": {"content": [{"text": self.text}]}},
                "usage": {"inputTokens": self.inp, "outputTokens": self.out,
                          "totalTokens": self.inp + self.out}}
