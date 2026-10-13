from django.contrib.auth.models import AbstractUser
from django.db import models

from base.defaults import DEFAULT_ON_DELETE
from base.enums import StorageProvider
from base.manager import UserManager
from base.model import DropdownBase, ModelBase
from base.storage import get_storage_service


# Create your models here.
class UploadFile(ModelBase):
    provider = models.CharField(max_length=20, choices=StorageProvider.choices, default=StorageProvider.LOCAL)
    key = models.CharField(max_length=500)
    url = models.URLField(max_length=1000, blank=True)
    original_filename = models.CharField(max_length=255, blank=True, default="")
    content_type = models.CharField(max_length=100, blank=True, default="")
    size = models.PositiveIntegerField(default=0)

    @classmethod
    def upload(cls, file, provider: str, filename: str | None = None) -> "UploadFile":
        "Server-side write path (Local, Cloudinary, and S3's export-zip task). Raises base.storage.StorageError on failure."
        result = get_storage_service(provider).upload_file(file, filename=filename or getattr(file, "name", ""))
        return cls.objects.create(
            provider=result.provider,
            key=result.key,
            url=result.url,
            original_filename=result.original_filename,
            content_type=result.content_type,
            size=result.size,
        )

    def open(self):
        "Server-side read path. Raises base.storage.StorageError on failure."
        return get_storage_service(self.provider).open_file(self)

    def get_url(self) -> str:
        "Client-facing URL -- fresh presigned GET for S3, stored value otherwise. Serializers use this, not `.url` directly."
        return get_storage_service(self.provider).get_url(self)


class MasterDropdown(DropdownBase):
    class Meta(DropdownBase.Meta):
        db_table = "core_dropdown"


class User(ModelBase, AbstractUser):
    USER_MODEL_FIELDS = ModelBase.BASE_MODEL_FIELDS + (
        "is_superuser",
        "last_login",
        "is_staff",
        "is_active",
        "date_joined",
        "groups",
        "user_permissions",
    )

    username = models.CharField(
        max_length=150,
        unique=True,
    )
    name = models.CharField(
        max_length=255,
    )
    email = models.EmailField(null=True)
    profile_image = models.ForeignKey(
        UploadFile,
        on_delete=DEFAULT_ON_DELETE,
        null=True,
        related_name="user_profile_images",
    )
    gender = models.ForeignKey(
        MasterDropdown,
        on_delete=DEFAULT_ON_DELETE,
        null=True,
        related_name="user_genders",
    )
    objects = UserManager()

    USERNAME_FIELD = "username"
