import uuid

from django.core.files.storage import Storage, storages


def submission_storage() -> Storage:
    """The storage of the delivered files: an S3 bucket (RustFS locally), see ADR 0002."""
    return storages["submissions"]


def new_storage_key(activity_id: int) -> str:
    """A random key: it never derives from the original name."""
    return f"entregas/{activity_id}/{uuid.uuid4().hex}"
