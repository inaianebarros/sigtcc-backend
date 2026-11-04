"""Smoke test for the Docker environment: RustFS (S3 through boto3) and Mailpit (SMTP and HTTP API).

Run inside Compose:

    docker compose run --rm backend python scripts/smoke_services.py

Exits with code 0 only when every step passes.
"""

import json
import os
import sys
import time
import urllib.request
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")


def smoke_storage() -> None:
    import boto3
    from botocore.config import Config

    client = boto3.client(
        "s3",
        endpoint_url=os.environ["AWS_S3_ENDPOINT_URL"],
        region_name=os.environ.get("AWS_S3_REGION_NAME", "us-east-1"),
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )
    bucket = os.environ.get("S3_BUCKET", "sigtcc-dev")
    key = f"smoke/{uuid.uuid4()}.bin"
    payload = os.urandom(256 * 1024)

    try:
        client.create_bucket(Bucket=bucket)
        print(f"[storage] bucket created: {bucket}")
    except client.exceptions.BucketAlreadyOwnedByYou:
        print(f"[storage] bucket already existed: {bucket}")
    client.put_object(Bucket=bucket, Key=key, Body=payload)
    print(f"[storage] object uploaded: {key} ({len(payload)} bytes)")
    downloaded = client.get_object(Bucket=bucket, Key=key)["Body"].read()
    if downloaded != payload:
        raise AssertionError("downloaded content differs from the uploaded content")
    print("[storage] object downloaded and identical to the upload")
    client.delete_object(Bucket=bucket, Key=key)
    remaining = client.list_objects_v2(Bucket=bucket, Prefix=key).get("KeyCount", 0)
    if remaining:
        raise AssertionError("object still exists after deletion")
    print("[storage] object deleted")


def smoke_mail() -> None:
    import django
    from django.core.mail import send_mail

    django.setup()
    api = os.environ.get("MAILPIT_API_URL", "http://mailpit:8025")
    subject = f"SIGTCC smoke {uuid.uuid4()}"
    sent = send_mail(
        subject,
        "Test e-mail from the development environment.",
        None,
        ["aluno.teste@example.com"],
    )
    if sent != 1:
        raise AssertionError("send_mail did not report a sent message")
    print(f"[mail] sent through SMTP: {subject}")

    for _ in range(10):
        with urllib.request.urlopen(f"{api}/api/v1/messages", timeout=5) as response:
            messages = json.load(response)["messages"]
        if any(message["Subject"] == subject for message in messages):
            print("[mail] message found in the Mailpit API")
            return
        time.sleep(0.5)
    raise AssertionError("message did not appear in Mailpit")


def main() -> int:
    failed = False
    for name, step in (("storage", smoke_storage), ("mail", smoke_mail)):
        try:
            step()
        except Exception as exc:
            failed = True
            print(f"[{name}] FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
    print("FAILED" if failed else "OK")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
