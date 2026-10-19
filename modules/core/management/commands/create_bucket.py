from botocore.exceptions import ClientError
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from base.enums import StorageProvider
from base.storage import get_storage_service


class Command(BaseCommand):
    help = (
        "Create the S3 bucket, if missing, with a CORS rule letting the frontend upload straight to it. "
        "For local S3 emulators (compose.yaml); a real bucket is set up by hand."
    )

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("create_bucket only runs with DEBUG on.")
        s3 = get_storage_service(StorageProvider.S3)
        try:
            s3.client.head_bucket(Bucket=s3.bucket)
            self.stdout.write(f"Bucket {s3.bucket} exists.")
        except ClientError:
            # us-east-1 is the one region S3 refuses as an explicit location.
            region = settings.AWS_S3_REGION_NAME
            location = {} if region == "us-east-1" else {"CreateBucketConfiguration": {"LocationConstraint": region}}
            s3.client.create_bucket(Bucket=s3.bucket, **location)
            self.stdout.write(self.style.SUCCESS(f"Created bucket {s3.bucket}."))
        # Browsers PUT uploads to presigned URLs and GET files back, from the frontend's origin.
        s3.client.put_bucket_cors(
            Bucket=s3.bucket,
            CORSConfiguration={
                "CORSRules": [
                    {
                        "AllowedOrigins": [settings.FRONTEND_URL],
                        "AllowedMethods": ["PUT", "GET"],
                        "AllowedHeaders": ["Content-Type"],
                    }
                ]
            },
        )
