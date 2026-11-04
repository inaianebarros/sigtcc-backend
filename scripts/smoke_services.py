"""Teste de fumaça do ambiente Docker: RustFS (S3 via boto3) e Mailpit (SMTP + API HTTP).

Fora da suíte de testes. Execute dentro do Compose:

    docker compose run --rm backend python scripts/smoke_services.py

Variáveis lidas do ambiente: AWS_S3_ENDPOINT_URL, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY,
AWS_S3_REGION_NAME, S3_BUCKET, EMAIL_HOST, EMAIL_PORT, MAILPIT_API_URL.
Sai com código 0 somente se todos os passos passarem.
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
        print(f"[storage] bucket criado: {bucket}")
    except client.exceptions.BucketAlreadyOwnedByYou:
        print(f"[storage] bucket já existia: {bucket}")
    client.put_object(Bucket=bucket, Key=key, Body=payload)
    print(f"[storage] objeto enviado: {key} ({len(payload)} bytes)")
    downloaded = client.get_object(Bucket=bucket, Key=key)["Body"].read()
    if downloaded != payload:
        raise AssertionError("conteúdo baixado difere do enviado")
    print("[storage] objeto baixado e idêntico ao enviado")
    client.delete_object(Bucket=bucket, Key=key)
    remaining = client.list_objects_v2(Bucket=bucket, Prefix=key).get("KeyCount", 0)
    if remaining:
        raise AssertionError("objeto ainda existe após a exclusão")
    print("[storage] objeto excluído")


def smoke_mail() -> None:
    import django
    from django.core.mail import send_mail

    django.setup()
    api = os.environ.get("MAILPIT_API_URL", "http://mailpit:8025")
    subject = f"Fumaça SIGTCC {uuid.uuid4()}"
    sent = send_mail(
        subject,
        "E-mail de teste do ambiente de desenvolvimento.",
        None,
        ["aluno.teste@example.com"],
    )
    if sent != 1:
        raise AssertionError("send_mail não reportou envio")
    print(f"[mail] enviado via SMTP: {subject}")

    for _ in range(10):
        with urllib.request.urlopen(f"{api}/api/v1/messages", timeout=5) as response:
            messages = json.load(response)["messages"]
        if any(message["Subject"] == subject for message in messages):
            print("[mail] mensagem encontrada na API do Mailpit")
            return
        time.sleep(0.5)
    raise AssertionError("mensagem não apareceu no Mailpit")


def main() -> int:
    failed = False
    for name, step in (("storage", smoke_storage), ("mail", smoke_mail)):
        try:
            step()
        except Exception as exc:  # relatório explícito do erro exato
            failed = True
            print(f"[{name}] FALHOU: {type(exc).__name__}: {exc}", file=sys.stderr)
    print("FALHA" if failed else "OK")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
