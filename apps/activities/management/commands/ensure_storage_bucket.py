from botocore.exceptions import ClientError
from django.core.management.base import BaseCommand

from apps.activities.storage import submission_storage

MISSING_BUCKET_CODES = {"404", "NoSuchBucket", "NotFound"}


class Command(BaseCommand):
    help = "Creates the bucket of the delivered files if it does not exist; safe to run again."

    def handle(self, *args, **options):
        storage = submission_storage()
        client = storage.connection.meta.client
        bucket = storage.bucket_name
        try:
            client.head_bucket(Bucket=bucket)
        except ClientError as error:
            if error.response["Error"]["Code"] not in MISSING_BUCKET_CODES:
                raise
            try:
                client.create_bucket(Bucket=bucket)
            except ClientError as creation_error:
                # Another instance created it between the check and the creation.
                if creation_error.response["Error"]["Code"] != "BucketAlreadyOwnedByYou":
                    raise
            self.stdout.write(f"Bucket criado: {bucket}")
        else:
            self.stdout.write(f"Bucket já existe: {bucket}")
