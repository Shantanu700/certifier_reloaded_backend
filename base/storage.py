import io
import os
import urllib.request
from urllib.error import HTTPError, URLError

import boto3
import cloudinary
import cloudinary.exceptions
import cloudinary.uploader
import uuid6
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings
from django.core.files.storage import default_storage
from pydantic import BaseModel

from base.enums import StorageProvider

# Write your storage services here


class StorageError(Exception):
    "Raised when a storage provider fails to upload/read a file."


class UploadResult(BaseModel):
    provider: str
    key: str
    url: str
    original_filename: str
    content_type: str
    size: int


class PresignedUpload(BaseModel):
    "Returned by S3's client-direct-upload flow -- see modules.core.api.v1.views.PresignUploadAV."

    url: str
    headers: dict[str, str]
    expires_in: int


def generate_storage_key(filename: str) -> str:
    return f"uploads/{uuid6.uuid6()}/{filename}"


class Service:
    "Base class for pluggable storage providers. Subclass, set `provider`, register in STORAGE_SERVICES."

    provider: str

    def upload_file(self, file, *, filename: str) -> UploadResult:
        "Server-side write: the server already holds the bytes and pushes them to the backend itself."
        raise NotImplementedError

    def open_file(self, upload) -> io.BytesIO:
        "Server-side read: fetch the bytes so app code (CSV parsing, rasterization, zipping) can process them."
        raise NotImplementedError

    def get_url(self, upload) -> str:
        "Client-facing URL for this file. May be a fresh, time-limited URL -- never cache the return value."
        raise NotImplementedError


class LocalStorageService(Service):
    provider = StorageProvider.LOCAL

    def upload_file(self, file, *, filename: str) -> UploadResult:
        key = generate_storage_key(filename)
        saved_name = default_storage.save(key, file)
        return UploadResult(
            provider=self.provider,
            key=saved_name,
            url=default_storage.url(saved_name),
            original_filename=filename,
            content_type=getattr(file, "content_type", "") or "",
            size=default_storage.size(saved_name),
        )

    def open_file(self, upload) -> io.BytesIO:
        try:
            with default_storage.open(upload.key, "rb") as fh:
                return io.BytesIO(fh.read())
        except FileNotFoundError as exc:
            raise StorageError(f"Local file not found for key '{upload.key}'.") from exc

    def get_url(self, upload) -> str:
        return upload.url


class S3StorageService(Service):
    provider = StorageProvider.S3

    def __init__(self):
        missing = [
            name
            for name, value in (
                ("AWS_ACCESS_KEY_ID", settings.AWS_ACCESS_KEY_ID),
                ("AWS_SECRET_ACCESS_KEY", settings.AWS_SECRET_ACCESS_KEY),
                ("AWS_STORAGE_BUCKET_NAME", settings.AWS_STORAGE_BUCKET_NAME),
            )
            if not value
        ]
        if missing:
            raise StorageError(f"Missing S3 setting(s): {', '.join(missing)}.")
        self.bucket = settings.AWS_STORAGE_BUCKET_NAME
        self.client = boto3.client(
            "s3",
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
            region_name=settings.AWS_S3_REGION_NAME,
            endpoint_url=settings.AWS_S3_ENDPOINT_URL,
            # B2 doesn't recognize custom regions like "us-east-005" and botocore falls back to
            # SigV2 presigned URLs for them, which B2 rejects outright -- force SigV4.
            config=Config(signature_version="s3v4"),
        )

    def generate_presigned_upload(
        self, key: str, content_type: str, allowed_types: dict[str, str], max_size: int
    ) -> PresignedUpload:
        "Client-direct upload: hand the caller a presigned PUT URL the browser/client can upload straight to S3."
        # B2's S3-compatible API doesn't implement POST-policy uploads (generate_presigned_post),
        # so max_size can't be enforced via a signed condition here -- confirm_upload() re-checks it after the fact.
        try:
            url = self.client.generate_presigned_url(
                "put_object",
                Params={"Bucket": self.bucket, "Key": key, "ContentType": content_type},
                ExpiresIn=settings.AWS_S3_PRESIGNED_URL_EXPIRY,
            )
        except (BotoCoreError, ClientError) as exc:
            raise StorageError(f"Failed to create presigned upload: {exc}") from exc
        return PresignedUpload(
            url=url,
            headers={"Content-Type": content_type},
            expires_in=settings.AWS_S3_PRESIGNED_URL_EXPIRY,
        )

    def confirm_upload(self, upload, allowed_types: dict[str, str], max_size: int) -> UploadResult:
        "Verify a client-direct upload actually landed in S3 and re-validate its type/size -- the presigned PUT can't enforce these itself, so this is the only real check."
        try:
            head = self.client.head_object(Bucket=self.bucket, Key=upload.key)
        except ClientError as exc:
            raise StorageError(f"Uploaded file not found in S3 for key '{upload.key}'.") from exc
        except BotoCoreError as exc:
            raise StorageError(f"Failed to verify S3 upload: {exc}") from exc

        content_type = head.get("ContentType", "")
        size = head.get("ContentLength", 0)
        extension = os.path.splitext(upload.original_filename)[1].lstrip(".").lower()
        if extension not in allowed_types:
            raise StorageError(f"Unsupported file type '.{extension}'. Allowed: {', '.join(allowed_types)}.")
        if content_type != allowed_types[extension]:
            raise StorageError("Uploaded file's content type does not match its extension.")
        if size > max_size:
            raise StorageError(f"Uploaded file too large. Max size is {max_size} bytes.")

        return UploadResult(
            provider=self.provider,
            key=upload.key,
            url="",
            original_filename=upload.original_filename,
            content_type=content_type,
            size=size,
        )

    def upload_file(self, file, *, filename: str) -> UploadResult:
        "Server-side write, used only by export_certificates_task (no client involved) -- still routed through a presigned PUT for consistency with the rest of this service."
        key = generate_storage_key(filename)
        content_type = getattr(file, "content_type", "") or "application/octet-stream"
        try:
            presigned_url = self.client.generate_presigned_url(
                "put_object",
                Params={"Bucket": self.bucket, "Key": key, "ContentType": content_type},
                ExpiresIn=settings.AWS_S3_PRESIGNED_URL_EXPIRY,
            )
        except (BotoCoreError, ClientError) as exc:
            raise StorageError(f"Failed to create presigned upload URL: {exc}") from exc

        body = file.read()
        request = urllib.request.Request(
            presigned_url, data=body, method="PUT", headers={"Content-Type": content_type}
        )
        try:
            with urllib.request.urlopen(request):
                pass
        except (URLError, HTTPError) as exc:
            raise StorageError(f"Failed to upload file to S3: {exc}") from exc

        return UploadResult(
            provider=self.provider,
            key=key,
            url="",
            original_filename=filename,
            content_type=content_type,
            size=len(body),
        )

    def open_file(self, upload) -> io.BytesIO:
        url = self.get_url(upload)
        try:
            with urllib.request.urlopen(url) as response:
                return io.BytesIO(response.read())
        except (URLError, HTTPError) as exc:
            raise StorageError(f"Failed to fetch S3 object for key '{upload.key}': {exc}") from exc

    def get_url(self, upload) -> str:
        try:
            return self.client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket, "Key": upload.key},
                ExpiresIn=settings.AWS_S3_PRESIGNED_URL_EXPIRY,
            )
        except (BotoCoreError, ClientError) as exc:
            raise StorageError(f"Failed to generate presigned URL for key '{upload.key}': {exc}") from exc


class CloudinaryStorageService(Service):
    "Only used server-side (Celery-generated certificates) -- never client-uploaded in this app."

    provider = StorageProvider.CLOUDINARY

    def __init__(self):
        missing = [
            name
            for name, value in (
                ("CLOUDINARY_CLOUD_NAME", settings.CLOUDINARY_CLOUD_NAME),
                ("CLOUDINARY_API_KEY", settings.CLOUDINARY_API_KEY),
                ("CLOUDINARY_API_SECRET", settings.CLOUDINARY_API_SECRET),
            )
            if not value
        ]
        if missing:
            raise StorageError(f"Missing Cloudinary setting(s): {', '.join(missing)}.")
        cloudinary.config(
            cloud_name=settings.CLOUDINARY_CLOUD_NAME,
            api_key=settings.CLOUDINARY_API_KEY,
            api_secret=settings.CLOUDINARY_API_SECRET,
            secure=True,
        )

    def upload_file(self, file, *, filename: str) -> UploadResult:
        public_id = os.path.splitext(generate_storage_key(filename))[0]
        try:
            result = cloudinary.uploader.upload(file, public_id=public_id, resource_type="auto")
        except cloudinary.exceptions.Error as exc:
            raise StorageError(f"Failed to upload file to Cloudinary: {exc}") from exc
        return UploadResult(
            provider=self.provider,
            key=result["public_id"],
            url=result["secure_url"],
            original_filename=filename,
            content_type=getattr(file, "content_type", "") or "",
            size=result.get("bytes", 0),
        )

    def open_file(self, upload) -> io.BytesIO:
        "No SDK 'download' analog to S3's get_object -- plain HTTPS GET against the stored secure_url instead."
        try:
            with urllib.request.urlopen(upload.url) as response:
                return io.BytesIO(response.read())
        except (URLError, HTTPError) as exc:
            raise StorageError(f"Failed to fetch Cloudinary asset '{upload.key}': {exc}") from exc

    def get_url(self, upload) -> str:
        return upload.url


STORAGE_SERVICES: dict[str, type[Service]] = {
    StorageProvider.LOCAL: LocalStorageService,
    StorageProvider.S3: S3StorageService,
    StorageProvider.CLOUDINARY: CloudinaryStorageService,
}


def get_storage_service(provider: str) -> Service:
    try:
        return STORAGE_SERVICES[provider]()
    except KeyError:
        raise StorageError(f"Unknown storage provider '{provider}'.")
