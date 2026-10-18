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
