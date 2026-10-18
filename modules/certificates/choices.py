from django.db.models.enums import TextChoices


class TextAlignChoices(TextChoices):
    LEFT = "left", "Left"
    CENTER = "center", "Center"
    RIGHT = "right", "Right"


class CertificateBatchStatusChoices(TextChoices):
    PENDING = "pending", "Pending"
    MAPPED = "mapped", "Mapped"
    QUEUED = "queued", "Queued"
    PROCESSING = "processing", "Processing"
    COMPLETED = "completed", "Completed"
    FAILED = "failed", "Failed"


class ExportStatusChoices(TextChoices):
    PENDING = "pending", "Pending"
    QUEUED = "queued", "Queued"
    PROCESSING = "processing", "Processing"
    COMPLETED = "completed", "Completed"
    FAILED = "failed", "Failed"


class IssueStatusChoices(TextChoices):
    DRAFT = "draft", "Draft"
    ISSUED = "issued", "Issued"
    REVOKED = "revoked", "Revoked"


class BoxTypeChoices(TextChoices):
    TEXT = "text", "Text"
    QR = "qr", "QR code"
    VERIFY_QR = "verify_qr", "Verification QR code"
    VERIFY_CODE = "verify_code", "Verification code"


# Boxes whose value comes from a CSV column (or typed values); the verify_* ones are filled from the certificate's code.
MAPPABLE_BOX_TYPES = (BoxTypeChoices.TEXT, BoxTypeChoices.QR)
QR_BOX_TYPES = (BoxTypeChoices.QR, BoxTypeChoices.VERIFY_QR)
