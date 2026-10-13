import os

from django.core.exceptions import ValidationError
from pydantic import BaseModel, ValidationError as PydanticValidationError
from rest_framework import serializers

# Write your validators here


def validate_with_schema(schema: type[BaseModel], value):
    "Validate a JSONField payload against a pydantic schema."
    try:
        schema.model_validate(value)
    except PydanticValidationError as exc:
        raise ValidationError(str(exc))


def validate_upload(value, allowed_types: dict[str, str], max_size: int):
    extension = os.path.splitext(value.name)[1].lstrip(".").lower()
    if extension not in allowed_types:
        raise serializers.ValidationError(
            f"Unsupported file type '.{extension}'. Allowed: {', '.join(allowed_types)}."
        )
    if value.content_type != allowed_types[extension]:
        raise serializers.ValidationError("File content does not match its extension.")
    if value.size > max_size:
        raise serializers.ValidationError(f"File too large. Max size is {max_size} bytes.")
