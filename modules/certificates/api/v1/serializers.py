import re

from django.conf import settings
from drf_spectacular.utils import PolymorphicProxySerializer, extend_schema_field
from rest_framework import serializers

from base.serializers import BaseSerializer
from base.validators import validate_with_schema
from modules.certificates.choices import CertificateBatchStatusChoices, TextAlignChoices
from modules.certificates.fonts import BUNDLED_FONTS, DEFAULT_FONT
from modules.certificates.models import (
    CertificateBatch,
    CertificateTemplate,
    GeneratedCertificate,
    TemplateBox,
    TemplateImageBox,
)
from modules.certificates.schemas import BoxMappingSchema, NamingConventionSchema

# Write your serializers here

ALLOWED_TEMPLATE_TYPES = {**settings.ALLOWED_IMG_TYPES, **settings.ALLOWED_PDF_TYPES}
HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


class CertificateTemplateSerializer(BaseSerializer):
    file_fields = ["file"]  # noqa: RUF012

    class Meta:
        model = CertificateTemplate
        exclude = ("owner",)


class CertificateTemplateUploadSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255)
    upload = serializers.UUIDField(help_text="uuid returned by POST core/upload-url/ after the file has been uploaded to S3.")


class CanvaTemplateImportSerializer(serializers.Serializer):
    design_id = serializers.CharField(max_length=100)
    page = serializers.IntegerField(min_value=1, default=1, help_text="1-indexed page of the Canva design to use.")
    name = serializers.CharField(max_length=255)


class CertificateTemplateUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255, required=False)
    upload = serializers.UUIDField(required=False, help_text="uuid returned by POST core/upload-url/ after the file has been uploaded to S3.")

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError("Provide at least 'name' or 'upload' to update.")
        return attrs


class TemplateBoxSerializer(BaseSerializer):
    class Meta:
        model = TemplateBox
        exclude = ("template",)


def _validate_font_color(value):
    if not HEX_COLOR_RE.match(value):
        raise serializers.ValidationError("font_color must be a hex color like '#000000'.")
    return value


class TemplateBoxCreateSerializer(serializers.Serializer):
    key = serializers.SlugField(max_length=100)
    x = serializers.IntegerField(min_value=0)
    y = serializers.IntegerField(min_value=0)
    width = serializers.IntegerField(min_value=1)
    height = serializers.IntegerField(min_value=1)
    font = serializers.ChoiceField(choices=list(BUNDLED_FONTS), required=False, default=DEFAULT_FONT)
    font_size = serializers.IntegerField(min_value=1, required=False, default=32)
    font_color = serializers.CharField(required=False, default="#000000")
    alignment = serializers.ChoiceField(
        choices=TextAlignChoices.choices,
        required=False,
        default=TextAlignChoices.CENTER,
    )

    def validate_font_color(self, value):
        return _validate_font_color(value)

    def validate_key(self, value):
        template = self.context["template"]
        if template.boxes.filter(key=value).exists():
            raise serializers.ValidationError(f"A box with key '{value}' already exists on this template.")
        return value


class TemplateBoxUpdateSerializer(serializers.Serializer):
    key = serializers.SlugField(max_length=100, required=False)
    x = serializers.IntegerField(min_value=0, required=False)
    y = serializers.IntegerField(min_value=0, required=False)
    width = serializers.IntegerField(min_value=1, required=False)
    height = serializers.IntegerField(min_value=1, required=False)
    font = serializers.ChoiceField(choices=list(BUNDLED_FONTS), required=False)
    font_size = serializers.IntegerField(min_value=1, required=False)
    font_color = serializers.CharField(required=False)
    alignment = serializers.ChoiceField(choices=TextAlignChoices.choices, required=False)

    def validate_font_color(self, value):
        return _validate_font_color(value)

    def validate_key(self, value):
        box = self.context["box"]
        if box.template.boxes.exclude(pk=box.pk).filter(key=value).exists():
            raise serializers.ValidationError(f"A box with key '{value}' already exists on this template.")
        return value

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError("Provide at least one field to update.")
        return attrs


class TemplateImageBoxSerializer(BaseSerializer):
    file_fields = ["image"]  # noqa: RUF012

    class Meta:
        model = TemplateImageBox
        exclude = ("template",)


class TemplateImageBoxCreateSerializer(serializers.Serializer):
    key = serializers.SlugField(max_length=100)
    x = serializers.IntegerField(min_value=0)
    y = serializers.IntegerField(min_value=0)
    width = serializers.IntegerField(min_value=1)
    height = serializers.IntegerField(min_value=1)
    upload = serializers.UUIDField(help_text="uuid returned by POST core/upload-url/ after the file has been uploaded to S3.")

    def validate_key(self, value):
        template = self.context["template"]
        if template.image_boxes.filter(key=value).exists():
            raise serializers.ValidationError(f"An image box with key '{value}' already exists on this template.")
        return value


class TemplateImageBoxUpdateSerializer(serializers.Serializer):
    key = serializers.SlugField(max_length=100, required=False)
    x = serializers.IntegerField(min_value=0, required=False)
    y = serializers.IntegerField(min_value=0, required=False)
    width = serializers.IntegerField(min_value=1, required=False)
    height = serializers.IntegerField(min_value=1, required=False)
    upload = serializers.UUIDField(required=False, help_text="uuid returned by POST core/upload-url/ after the file has been uploaded to S3.")

    def validate_key(self, value):
        image_box = self.context["image_box"]
        if image_box.template.image_boxes.exclude(pk=image_box.pk).filter(key=value).exists():
            raise serializers.ValidationError(f"An image box with key '{value}' already exists on this template.")
        return value

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError("Provide at least one field to update.")
        return attrs


class CertificateBatchSerializer(BaseSerializer):
    file_fields = ["file", "export_file"]  # noqa: RUF012

    class Meta:
        model = CertificateBatch
        exclude = ("owner", "template")


class CertificateBatchProgressSerializer(serializers.Serializer):
    "Minimal poll payload -- no mapping/csv_columns/file, just enough to drive a progress bar."

    generation_status = serializers.ChoiceField(choices=CertificateBatchStatusChoices.choices)
    total_rows = serializers.IntegerField()
    processed_rows = serializers.IntegerField()


class CertificateBatchExportProgressSerializer(BaseSerializer):
    "Minimal poll payload for the export job, plus the resulting zip's download link once ready."

    file_fields = ["export_file"]  # noqa: RUF012

    class Meta:
        model = CertificateBatch
        fields = ["export_status", "total_rows", "export_processed_rows", "export_error_message", "export_file"]


class CertificateBatchUploadSerializer(serializers.Serializer):
    upload = serializers.UUIDField(help_text="uuid returned by POST core/upload-url/ after the file has been uploaded to S3.")


class CertificateBatchMappingSerializer(serializers.Serializer):
    mapping = serializers.DictField(child=serializers.CharField(allow_blank=True))

    def validate_mapping(self, value):
        validate_with_schema(BoxMappingSchema, value)
        return value


class CSVKeyPartSerializer(serializers.Serializer):
    type = serializers.ChoiceField(choices=["csv_key"])
    key = serializers.CharField(help_text="CSV column name to pull this component's value from.")


class SequencePartSerializer(serializers.Serializer):
    type = serializers.ChoiceField(choices=["sequence"])
    start = serializers.IntegerField(required=False, default=1)
    step = serializers.IntegerField(required=False, default=1)


class RandomPartSerializer(serializers.Serializer):
    type = serializers.ChoiceField(choices=["random"])
    kind = serializers.ChoiceField(choices=["string", "number"], required=False, default="string")
    length = serializers.IntegerField(required=False, default=6, min_value=1, max_value=32)


class FixedPartSerializer(serializers.Serializer):
    type = serializers.ChoiceField(choices=["fixed"])
    value = serializers.CharField(help_text="Literal text to insert as-is.")


@extend_schema_field(
    PolymorphicProxySerializer(
        component_name="NamingPart",
        serializers={
            "csv_key": CSVKeyPartSerializer,
            "sequence": SequencePartSerializer,
            "random": RandomPartSerializer,
            "fixed": FixedPartSerializer,
        },
        resource_type_field_name="type",
        many=True,
    )
)
class NamingConventionField(serializers.ListField):
    child = serializers.DictField()


class CertificateBatchNamingSerializer(serializers.Serializer):
    naming_convention = NamingConventionField(allow_empty=False, allow_null=True)

    def validate_naming_convention(self, value):
        if value is not None:
            validate_with_schema(NamingConventionSchema, value)
        return value


class GeneratedCertificateSerializer(BaseSerializer):
    file_fields = ["file"]  # noqa: RUF012

    class Meta:
        model = GeneratedCertificate
        exclude = ("batch",)
