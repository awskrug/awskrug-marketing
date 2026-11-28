"""API 테스트용 가짜 AWS 클라이언트."""

import copy
import json
import re

from botocore.exceptions import ClientError


class FakeClientError(Exception):
    pass


class ConditionalCheckFailed(ClientError):
    def __init__(self):
        super().__init__({"Error": {"Code": "ConditionalCheckFailedException", "Message": "x"}}, "UpdateItem")


def _resolve(name, names):
    return names.get(name, name)


class FakeTable:
    """update_item은 이 코드가 쓰는 SET 식과 조건 식만 해석한다."""

    def __init__(self, key):
        self.key = key
        self.items = {}
        self.updates = []

    def put_item(self, Item, **kw):
        self.items[Item[self.key]] = copy.deepcopy(Item)

    def get_item(self, Key, **kw):
        item = self.items.get(Key[self.key])
        return {"Item": copy.deepcopy(item)} if item is not None else {}

    def scan(self, **kw):
        return {"Items": [copy.deepcopy(i) for i in self.items.values()]}

    def _check(self, item, cond, names, values):
        if not cond:
            return True
        for part in cond.split(" AND "):
            part = part.strip()
            m = re.fullmatch(r"attribute_(not_)?exists\((.+)\)", part)
            if m:
                path = _resolve(m.group(2), names)
                cur = item
                for p in path.split("."):
                    cur = cur.get(_resolve(p, names)) if isinstance(cur, dict) else None
                present = cur is not None
                if present == bool(m.group(1)):
                    return False
                continue
            m = re.fullmatch(r"(\S+) = (:\w+)", part)
            if m:
                if item.get(_resolve(m.group(1), names)) != values[m.group(2)]:
                    return False
                continue
            raise AssertionError(f"unsupported condition: {part}")
        return True

    def delete_item(self, Key, ConditionExpression=None, ExpressionAttributeNames=None,
                    ExpressionAttributeValues=None, **kw):
        item = self.items.get(Key[self.key])
        if ConditionExpression and (item is None or not self._check(
                item, ConditionExpression, ExpressionAttributeNames or {}, ExpressionAttributeValues or {})):
            raise ConditionalCheckFailed()
        self.items.pop(Key[self.key], None)

    def update_item(self, Key, UpdateExpression, ExpressionAttributeValues=None,
                    ExpressionAttributeNames=None, ConditionExpression=None, **kw):
        names = ExpressionAttributeNames or {}
        values = ExpressionAttributeValues or {}
        existing = self.items.get(Key[self.key])
        item = existing if existing is not None else {self.key: Key[self.key]}
        if existing is None and not (ConditionExpression or "").count("attribute_not_exists"):
            if ConditionExpression:
                raise ConditionalCheckFailed()
        if not self._check(item, ConditionExpression, names, values):
            raise ConditionalCheckFailed()
        self.updates.append({"expr": UpdateExpression, "values": values, "names": names,
                             "cond": ConditionExpression})
        assert UpdateExpression.startswith("SET ")
        for assignment in re.split(r",\s*(?![^()]*\))", UpdateExpression[4:]):
            target, _, expr = assignment.partition(" = ")
            m = re.fullmatch(r"if_not_exists\((.+?), (:\w+)\)", expr.strip())
            path = [_resolve(p, names) for p in target.strip().split(".")]
            cur = item
            for p in path[:-1]:
                cur = cur[p]
            if m:
                cur.setdefault(path[-1], copy.deepcopy(values[m.group(2)]))
            else:
                cur[path[-1]] = copy.deepcopy(values[expr.strip()])
        self.items[Key[self.key]] = item


class FakeDynamo:
    def __init__(self):
        self.tables = {}

    def Table(self, name):
        return self.tables.setdefault(name, FakeTable("githubId" if "sers" in name else "jobId"))


class FakeS3:
    def __init__(self):
        self.objects = set()
        self.presigned = []

    def generate_presigned_url(self, op, Params, ExpiresIn):
        self.presigned.append((op, Params, ExpiresIn))
        return f"https://s3.example/{Params['Key']}?op={op}&exp={ExpiresIn}"

    def list_objects_v2(self, Bucket, Prefix, **kw):
        return {"Contents": [{"Key": k} for k in sorted(self.objects) if k.startswith(Prefix)], "IsTruncated": False}

    def delete_objects(self, Bucket, Delete):
        for o in Delete["Objects"]:
            self.objects.discard(o["Key"])
        return {}

    def head_object(self, Bucket, Key):
        if Key not in self.objects:
            from botocore.exceptions import ClientError

            raise ClientError({"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject")
        return {}


class FakeSfn:
    def __init__(self):
        self.started = []
        self.fail = False

    def start_execution(self, stateMachineArn, input, name=None):
        if self.fail:
            raise RuntimeError("boom")
        self.started.append((stateMachineArn, json.loads(input)))
        return {"executionArn": "arn:exec"}


class FakeSecrets:
    def __init__(self, secret):
        self.secret = secret
        self.calls = 0

    def get_secret_value(self, SecretId):
        self.calls += 1
        return {"SecretString": json.dumps(self.secret)}
