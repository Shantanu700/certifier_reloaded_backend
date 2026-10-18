import re
from datetime import date, datetime

from django.db.models import Count, Q
from django.utils import timezone

from base.enums import RoleEnum, Status
from modules.certificates.choices import MAPPABLE_BOX_TYPES, CertificateBatchStatusChoices, IssueStatusChoices
from modules.certificates.models import CertificateBatch, GeneratedCertificate
from modules.certificates.rendering import load_prepared_image_box, render_file
from modules.certificates.verification import new_code
from modules.core.models import User

# Recipient fields a CSV column can be matched against.
MATCH_FIELDS = ("email", "username", "roll_number", "phone", "date_of_birth", "institution")
DATE_FORMATS = ("%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y", "%Y-%m-%d")

# Review buckets for an issuer, used for both the list filter and the counts.
FILTERS = {
    "unassigned": Q(issue_status=IssueStatusChoices.DRAFT, is_successful=True, recipient__isnull=True),
    "ready": Q(issue_status=IssueStatusChoices.DRAFT, is_successful=True, recipient__isnull=False),
    "issued": Q(issue_status=IssueStatusChoices.ISSUED),
    "revoked": Q(issue_status=IssueStatusChoices.REVOKED),
    "failed": Q(issue_status=IssueStatusChoices.DRAFT, is_successful=False),
}


class IssuanceError(Exception):
    "Message is safe to show the user."


def normalise_phone(value: str) -> str:
    return re.sub(r"\D", "", value)[-10:]


def parse_date(value: str) -> date | None:
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def verified_recipients():
    # Profiles are self-entered; only accounts that proved their email can receive certificates.
    return User.objects.filter(role=RoleEnum.RECIPIENT, email_verified=True, is_active=True)


def find_recipient(identifier: str) -> User:
    "A verified recipient by exact email or username -- no fuzzy search, so issuers can't browse students."
    identifier = identifier.strip()
    hits = list(verified_recipients().filter(Q(email__iexact=identifier) | Q(username__iexact=identifier))[:2])
    if not hits:
        raise IssuanceError(f"No verified recipient has the email or username “{identifier}”.")
    if len(hits) > 1:
        raise IssuanceError(f"More than one recipient matches “{identifier}”. Use their username.")
    return hits[0]


class IssuanceService:
    "Matching, issuing and revoking the certificates of one batch."

    def __init__(self, batch: CertificateBatch):
        self.batch = batch

    @classmethod
    def for_manual(cls, template) -> "IssuanceService":
        "The template's batch for certificates issued by hand (one per template, no CSV)."
        batch, _ = CertificateBatch.objects.get_or_create(
            template=template,
            is_manual=True,
            defaults={"owner": template.owner, "generation_status": CertificateBatchStatusChoices.COMPLETED},
        )
        return cls(batch)

    def counts(self) -> dict[str, int]:
        return self.batch.certificates.aggregate(**{name: Count("id", filter=q) for name, q in FILTERS.items()})

    def match(self, match_fields: dict[str, str], overwrite: bool = False) -> dict[str, int]:
        "Propose a recipient for each draft row. Rows already assigned are left alone unless `overwrite`."
        if self.batch.is_manual:
            raise IssuanceError("Certificates issued by hand already have a recipient.")
        if not match_fields:
            raise IssuanceError("Choose at least one recipient field to match on.")
        unknown = set(match_fields) - set(MATCH_FIELDS)
        if unknown:
            raise IssuanceError(f"Unknown recipient field(s): {', '.join(sorted(unknown))}.")
        missing = set(match_fields.values()) - set(self.batch.csv_columns)
        if missing:
            raise IssuanceError(f"Unknown CSV column(s): {', '.join(sorted(missing))}.")

        self.batch.match_fields = match_fields
        self.batch.save(update_fields=["match_fields"])

        targets = self.batch.certificates.filter(issue_status=IssueStatusChoices.DRAFT, is_successful=True)
        if not overwrite:
            targets = targets.filter(recipient__isnull=True)
        recipients = verified_recipients()
        certificates = list(targets)
        # ponytail: one query per row; batch the lookups or move to Celery if large CSVs get slow.
        for certificate in certificates:
            certificate.recipient, certificate.match_error = self._match_row(certificate.row_data, recipients)
        GeneratedCertificate.objects.bulk_update(certificates, ["recipient", "match_error"], batch_size=500)
        return self.counts()

    def _match_row(self, row: dict, recipients) -> tuple[User | None, str]:
        "A row matches when every mapped field equals the recipient's, and exactly one recipient does."
        lookup = {}
        for field, column in self.batch.match_fields.items():
            value = (row.get(column) or "").strip()
            if not value:
                return None, f"{column} is empty"
            if field == "phone":
                lookup["phone"] = normalise_phone(value)
                if len(lookup["phone"]) < 10:
                    return None, f"{column} isn't a 10-digit phone number"
            elif field == "date_of_birth":
                lookup["date_of_birth"] = parse_date(value)
                if lookup["date_of_birth"] is None:
                    return None, f"{column} isn't a date like 31-12-2004"
            else:
                lookup[f"{field}__iexact"] = value

        hits = list(recipients.filter(**lookup)[:2])
        if not hits:
            return None, "No verified recipient matches"
        if len(hits) > 1:
            return None, "More than one recipient matches"
        return hits[0], ""

    def issue(self) -> dict[str, int]:
        issued = self.batch.certificates.filter(FILTERS["ready"]).update(
            issue_status=IssueStatusChoices.ISSUED, issued_at=timezone.now()
        )
        if not issued:
            raise IssuanceError("No certificates are ready to issue. Assign recipients first.")
        return self.counts()

    def revoke(self) -> dict[str, int]:
        revoked = self.batch.certificates.filter(FILTERS["issued"]).update(
            issue_status=IssueStatusChoices.REVOKED, revoked_at=timezone.now()
        )
        if not revoked:
            raise IssuanceError("No issued certificates to revoke.")
        return self.counts()

    def assign(self, certificate: GeneratedCertificate, identifier: str | None):
        "Set (or clear, with None/blank) the recipient of a draft."
        if certificate.issue_status != IssueStatusChoices.DRAFT:
            raise IssuanceError(
                "An issued certificate can't move to someone else. Revoke it and issue a new one instead."
            )
        if identifier and not certificate.is_successful:
            raise IssuanceError("This certificate failed to render. Fix its values first.")
        certificate.recipient = find_recipient(identifier) if identifier else None
        certificate.match_error = ""
        certificate.save(update_fields=["recipient", "match_error"])

    def edit_values(self, certificate: GeneratedCertificate, values: dict[str, str]):
        "Change box text and re-render. Issued certificates update in place for their recipient."
        if certificate.issue_status == IssueStatusChoices.REVOKED:
            raise IssuanceError("A revoked certificate can't be changed.")
        boxes = self._boxes()
        unknown = set(values) - {box.key for box in boxes if box.type in MAPPABLE_BOX_TYPES}
        if unknown:
            raise IssuanceError(f"Unknown box key(s): {', '.join(sorted(unknown))}.")
        unmapped = sorted(key for key in values if not self.batch.mapping.get(key))
        if unmapped:
            raise IssuanceError(f"Box(es) {', '.join(unmapped)} aren't mapped to a CSV column, so they have no text.")

        row = dict(certificate.row_data)
        for key, value in values.items():
            row[self.batch.mapping[key]] = value
        previous = certificate.file
        certificate.verification_code = certificate.verification_code or new_code()  # older certificates have none
        certificate.file, warnings = self._render(certificate.row_index, row, boxes, certificate.verification_code)
        certificate.row_data = row
        certificate.is_successful = True
        certificate.error_message = "; ".join(warnings)
        certificate.save()
        if previous:
            previous.status = Status.DELETED
            previous.save()

    def revoke_one(self, certificate: GeneratedCertificate):
        if certificate.issue_status != IssueStatusChoices.ISSUED:
            raise IssuanceError("Only an issued certificate can be revoked.")
        certificate.issue_status = IssueStatusChoices.REVOKED
        certificate.revoked_at = timezone.now()
        certificate.save(update_fields=["issue_status", "revoked_at"])

    def delete(self, certificate: GeneratedCertificate):
        if certificate.issue_status != IssueStatusChoices.DRAFT:
            raise IssuanceError("Only draft certificates can be deleted. Revoke an issued one instead.")
        certificate.status = Status.DELETED
        certificate.save(update_fields=["status"])

    def issue_one(self, identifier: str, values: dict[str, str]) -> GeneratedCertificate:
        "Render a single certificate from typed values and issue it straight away (manual batch only)."
        recipient = find_recipient(identifier)
        boxes = self._boxes()
        mappable = [box for box in boxes if box.type in MAPPABLE_BOX_TYPES]
        unknown = set(values) - {box.key for box in mappable}
        if unknown:
            raise IssuanceError(f"Unknown box key(s): {', '.join(sorted(unknown))}.")
        row = {box.key: values.get(box.key, "") for box in mappable}

        batch = CertificateBatch.objects.select_for_update().get(pk=self.batch.pk)  # serialises row_index
        index = batch.total_rows
        code = new_code()
        upload, warnings = self._render(index, row, boxes, code)
        certificate = GeneratedCertificate.objects.create(
            batch=batch,
            row_index=index,
            row_data=row,
            file=upload,
            is_successful=True,
            error_message="; ".join(warnings),
            recipient=recipient,
            issue_status=IssueStatusChoices.ISSUED,
            issued_at=timezone.now(),
            verification_code=code,
        )
        batch.total_rows = batch.processed_rows = index + 1
        batch.save(update_fields=["total_rows", "processed_rows"])
        return certificate

    def _boxes(self):
        boxes = list(self.batch.template.boxes.all())
        if self.batch.is_manual:
            # A manual row is keyed by box key, so its mapping is the identity over the template's current boxes.
            if not boxes:
                raise IssuanceError("Add a text box to the template first.")
            self.batch.mapping = {box.key: box.key for box in boxes if box.type in MAPPABLE_BOX_TYPES}
            self.batch.csv_columns = list(self.batch.mapping)
            self.batch.save(update_fields=["mapping", "csv_columns"])
        return boxes

    def _render(self, index: int, row: dict, boxes, code: str):
        try:
            image_boxes = [load_prepared_image_box(box) for box in self.batch.template.image_boxes.all()]
            return render_file(self.batch, index, row, boxes, image_boxes, code)
        except Exception as exc:
            raise IssuanceError(f"Couldn't render the certificate: {exc}") from exc
