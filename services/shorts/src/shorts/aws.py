"""AWS 호출: S3 업로드, Transcribe 작업, Bedrock 호출. boto3는 make_clients 안에서만 import한다."""

import json
import os
import time


def make_clients(profile: str | None, region: str) -> dict:
    import boto3  # 지연 import: 테스트는 boto3 없이 동작한다.

    session = boto3.Session(profile_name=profile, region_name=region)
    return {
        "s3": session.client("s3"),
        "transcribe": session.client("transcribe"),
        "bedrock": session.client("bedrock-runtime"),
    }


def transcribe_file(
    media_path: str,
    bucket: str,
    job_name: str,
    s3,
    transcribe,
    poll_sec: float = 10,
    timeout_sec: float = 3600,
) -> dict:
    key = f"input/{job_name}/{os.path.basename(media_path)}"
    s3.upload_file(media_path, bucket, key)
    output_key = f"transcripts/{job_name}.json"
    transcribe.start_transcription_job(
        TranscriptionJobName=job_name,
        LanguageCode="ko-KR",
        Media={"MediaFileUri": f"s3://{bucket}/{key}"},
        OutputBucketName=bucket,
        OutputKey=output_key,
    )
    deadline = time.monotonic() + timeout_sec
    while True:
        job = transcribe.get_transcription_job(TranscriptionJobName=job_name)["TranscriptionJob"]
        status = job["TranscriptionJobStatus"]
        if status == "COMPLETED":
            body = s3.get_object(Bucket=bucket, Key=output_key)["Body"].read()
            return json.loads(body)
        if status == "FAILED":
            raise RuntimeError(f"Transcribe job {job_name} failed: {job.get('FailureReason')}")
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Transcribe job {job_name} not finished within {timeout_sec}s")
        time.sleep(poll_sec)


def ask_model(prompt: str, model_id: str, bedrock, max_tokens: int = 4096) -> dict:
    resp = bedrock.converse(
        modelId=model_id,
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        inferenceConfig={"maxTokens": max_tokens},
    )
    blocks = resp["output"]["message"]["content"]
    text = "".join(b["text"] for b in blocks if "text" in b)
    usage = resp.get("usage", {})
    inp = usage.get("inputTokens", 0)
    out = usage.get("outputTokens", 0)
    total = usage.get("totalTokens", 0)
    if total > inp + out:
        out += total - (inp + out)  # 추론 토큰은 출력 토큰으로 합산
    return {"text": text, "input_tokens": inp, "output_tokens": out}
