from django.db import models
from django.contrib.auth.models import AbstractUser

from base.defaults import DEFAULT_ON_DELETE
from base.manager import UserManager
from base.model import DropdownBase, ModelBase


# Create your models here.
def upload_file(instance, filename: str):
    return "uploads/{0}/{1}".format(instance.uuid, filename)


class UploadFile(ModelBase):
    file = models.FileField(upload_to=upload_file)


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
