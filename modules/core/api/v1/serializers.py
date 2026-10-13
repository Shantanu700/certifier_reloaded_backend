from django.conf import settings
from rest_framework import serializers

from base.serializers import BaseSerializer
from modules.core.models import MasterDropdown, User

# Write your serializers here

UPLOAD_CATEGORIES = {
    "template": {**settings.ALLOWED_IMG_TYPES, **settings.ALLOWED_PDF_TYPES},
    "image_box": settings.ALLOWED_IMG_TYPES,
    "csv_batch": settings.ALLOWED_CSV_TYPES,
}


class DropDownSerializer(BaseSerializer):
    class Meta:
        model = MasterDropdown
        fields = "__all__"


class LoginSerializer(BaseSerializer):
    file_fields = ["profile_image"]  # noqa: RUF012

    class Meta:
        model = User
        exclude = User.USER_MODEL_FIELDS + ("password","gender")


class PresignUploadRequestSerializer(serializers.Serializer):
    "Request body for POST core/upload-url/ -- validates the *declared* filename/content_type before any bytes exist."

    filename = serializers.CharField(max_length=255)
    content_type = serializers.CharField(max_length=100)
    category = serializers.ChoiceField(choices=list(UPLOAD_CATEGORIES))

    def validate(self, attrs):
        allowed_types = UPLOAD_CATEGORIES[attrs["category"]]
        extension = attrs["filename"].rsplit(".", 1)[-1].lower() if "." in attrs["filename"] else ""
        if extension not in allowed_types:
            raise serializers.ValidationError(
                f"Unsupported file type '.{extension}'. Allowed: {', '.join(allowed_types)}."
            )
        if attrs["content_type"] != allowed_types[extension]:
            raise serializers.ValidationError("content_type does not match the file's extension.")
        attrs["allowed_types"] = allowed_types
        return attrs


class PresignUploadResponseSerializer(serializers.Serializer):
    upload_uuid = serializers.UUIDField()
    url = serializers.URLField()
    headers = serializers.DictField(child=serializers.CharField())
    expires_in = serializers.IntegerField()
