from enum import IntEnum, StrEnum

from django.db.models import TextChoices

# Write your enums here


class Status(IntEnum):
    CREATED = 0
    UPDATED = 1
    DELETED = 2

class RoleEnum(StrEnum):
    EMPLOYEE = "employee"
    HR = "hr"


class StorageProvider(TextChoices):
    LOCAL = "local", "Local"
    S3 = "s3", "S3"
    CLOUDINARY = "cloudinary", "Cloudinary"
