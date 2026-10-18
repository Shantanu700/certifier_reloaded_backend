from django.conf import settings
from django.db import models
from django.db.models import Q

from base.defaults import DEFAULT_ON_DELETE
from base.enums import Status
from base.model import ModelBase
from modules.certificates.choices import (
    BoxTypeChoices,
    CertificateBatchStatusChoices,
    ExportStatusChoices,
    IssueStatusChoices,
    TextAlignChoices,
)
from modules.certificates.fonts import DEFAULT_FONT, FONT_CHOICES
from modules.certificates.validators import (
    validate_csv_columns,
    validate_mapping,
    validate_naming_convention,
    validate_row_data,
)
from modules.core.models import UploadFile

# Create your models here.


class CertificateTemplate(ModelBase):
    name = models.CharField(max_length=255)
    file = models.ForeignKey(UploadFile, on_delete=DEFAULT_ON_DELETE, related_name="+")
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=DEFAULT_ON_DELETE,
        related_name="certificate_templates",
    )
    # Set when the template was imported from Canva, so it can be re-synced from the same design page.
    canva_design_id = models.CharField(max_length=100, blank=True, default="")
    canva_page = models.PositiveIntegerField(null=True, blank=True)


class TemplateBox(ModelBase):
    template = models.ForeignKey(CertificateTemplate, on_delete=DEFAULT_ON_DELETE, related_name="boxes")
    key = models.SlugField(max_length=100)
    x = models.PositiveIntegerField()
    y = models.PositiveIntegerField()
    width = models.PositiveIntegerField()
    height = models.PositiveIntegerField()
    font = models.CharField(max_length=50, choices=FONT_CHOICES, default=DEFAULT_FONT)
    font_size = models.PositiveIntegerField(default=32)
    font_color = models.CharField(max_length=7, default="#000000")
    alignment = models.CharField(max_length=6, choices=TextAlignChoices.choices, default=TextAlignChoices.CENTER)
    # QR types draw a QR code in `font_color`; font, size and alignment only apply to text and the verification code.
    type = models.CharField(max_length=12, choices=BoxTypeChoices.choices, default=BoxTypeChoices.TEXT)

    class Meta(ModelBase.Meta):
        constraints = [
            models.UniqueConstraint(fields=["template", "key"], condition=Q(status__in=[Status.CREATED, Status.UPDATED]), name="unique_box_key_per_template"),
        ]


class TemplateImageBox(ModelBase):
    "A static image (signature/logo) positioned on a template; unlike TemplateBox, its value never comes from CSV data."

    template = models.ForeignKey(CertificateTemplate, on_delete=DEFAULT_ON_DELETE, related_name="image_boxes")
    key = models.SlugField(max_length=100)
    x = models.PositiveIntegerField()
    y = models.PositiveIntegerField()
    width = models.PositiveIntegerField()
    height = models.PositiveIntegerField()
    image = models.ForeignKey(UploadFile, on_delete=DEFAULT_ON_DELETE, related_name="+")

    class Meta(ModelBase.Meta):
        constraints = [
            models.UniqueConstraint(fields=["template", "key"], condition=Q(status__in=[Status.CREATED, Status.UPDATED]), name="unique_image_box_key_per_template"),
        ]


class CertificateBatch(ModelBase):
    "A CSV of recipient data bound to a template, plus its column->box mapping and generation state."

    template = models.ForeignKey(CertificateTemplate, on_delete=DEFAULT_ON_DELETE, related_name="batches")
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=DEFAULT_ON_DELETE,
        related_name="certificate_batches",
    )
    # Null only for the per-template manual batch (`is_manual`), which has no CSV.
    file = models.ForeignKey(UploadFile, on_delete=DEFAULT_ON_DELETE, null=True, related_name="+")
    is_manual = models.BooleanField(default=False)
    csv_columns = models.JSONField(default=list, validators=[validate_csv_columns])
    mapping = models.JSONField(default=dict, validators=[validate_mapping])
    naming_convention = models.JSONField(null=True, validators=[validate_naming_convention])
    generation_status = models.CharField(
        max_length=20, choices=CertificateBatchStatusChoices.choices, default=CertificateBatchStatusChoices.PENDING
    )
    total_rows = models.PositiveIntegerField(default=0)
    processed_rows = models.PositiveIntegerField(default=0)
    error_message = models.TextField(blank=True, default="")
    export_status = models.CharField(
        max_length=20, choices=ExportStatusChoices.choices, default=ExportStatusChoices.PENDING
    )
    export_processed_rows = models.PositiveIntegerField(default=0)
    export_error_message = models.TextField(blank=True, default="")
    export_file = models.ForeignKey(UploadFile, on_delete=DEFAULT_ON_DELETE, null=True, related_name="+")
    match_fields = models.JSONField(default=dict, blank=True, help_text="{recipient_field: csv_column} used to find recipients.")


class GeneratedCertificate(ModelBase):
    "One rendered certificate produced from a single row of a `CertificateBatch`'s CSV."

    batch = models.ForeignKey(CertificateBatch, on_delete=DEFAULT_ON_DELETE, related_name="certificates")
    row_index = models.PositiveIntegerField()
    row_data = models.JSONField(default=dict, validators=[validate_row_data])
    file = models.ForeignKey(UploadFile, on_delete=DEFAULT_ON_DELETE, null=True, related_name="+")
    is_successful = models.BooleanField(default=False)
    error_message = models.TextField(blank=True, default="")
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=DEFAULT_ON_DELETE,
        null=True,
        blank=True,
        related_name="received_certificates",
    )
    issue_status = models.CharField(max_length=20, choices=IssueStatusChoices.choices, default=IssueStatusChoices.DRAFT)
    match_error = models.CharField(max_length=255, blank=True, default="", help_text="Why matching left it unassigned.")
    issued_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    # Printed by verify_* boxes and looked up by the public verify page. Null on certificates made before it existed.
    verification_code = models.CharField(max_length=12, unique=True, null=True, blank=True)
