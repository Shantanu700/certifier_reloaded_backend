from django.conf import settings
from django.contrib.auth.password_validation import validate_password
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token
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
        exclude = User.USER_MODEL_FIELDS + ("password", "gender", "google_id")


class RegisterSerializer(BaseSerializer):
    password = serializers.CharField(write_only=True, style={"input_type": "password"})
    email = serializers.EmailField()

    class Meta:
        model = User
        fields = ("username", "name", "email", "password")

    def validate_email(self, value):
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("An account with this email already exists.")
        return value

    def validate_password(self, value):
        validate_password(value)
        return value

    def create(self, validated_data):
        return User.objects.create_user(**validated_data)


class GoogleAuthSerializer(serializers.Serializer):
    id_token = serializers.CharField(write_only=True)

    def validate(self, attrs):
        try:
            claims = google_id_token.verify_oauth2_token(
                attrs["id_token"],
                google_requests.Request(),
                settings.GOOGLE_CLIENT_ID,
            )
        except ValueError:
            raise serializers.ValidationError("Google sign-in failed. Please try again.")
        if not claims.get("email_verified"):
            raise serializers.ValidationError("Your Google email address is not verified.")
        attrs["id_token"] = claims
        return attrs


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
