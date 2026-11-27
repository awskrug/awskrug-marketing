"""Lambda 진입점. 표준 라이브러리와 boto3만 쓰며 ffmpeg가 필요한 모듈은 import하지 않는다."""

import json
import os

from . import aws, candidates, store, transcript

DEFAULT_MODEL_ID = "global.anthropic.claude-sonnet-4-6"


def select_candidates(event, context=None, *, s3=None, table=None, bedrock=None) -> dict:
    """전사 결과에서 후보를 골라 Jobs 테이블에 기록한다. event: {"jobId": ...}"""
    job_id = event["jobId"]
    bucket = os.environ["MEDIA_BUCKET"]
    if s3 is None or table is None or bedrock is None:
        import boto3

        s3 = s3 or boto3.client("s3")
        bedrock = bedrock or boto3.client("bedrock-runtime")
        table = table or boto3.resource("dynamodb").Table(os.environ["JOBS_TABLE"])

    store.set_status(table, job_id, "SELECTING")
    body = s3.get_object(Bucket=bucket, Key=f"jobs/{job_id}/transcript.json")["Body"].read()
    words = transcript.parse_transcribe(json.loads(body))
    sentences = transcript.to_sentences(words)

    model_id = os.environ.get("SHORTS_BEDROCK_MODEL_ID") or DEFAULT_MODEL_ID
    resp = aws.ask_model(candidates.build_prompt(sentences), model_id, bedrock)
    found = candidates.parse_response(resp["text"], sentences)
    usage = {"model_id": model_id, "input_tokens": resp["input_tokens"],
             "output_tokens": resp["output_tokens"]}
    store.set_candidates(table, job_id, found, usage)
    store.set_status(table, job_id, "READY")
    return {"jobId": job_id, "count": len(found)}
