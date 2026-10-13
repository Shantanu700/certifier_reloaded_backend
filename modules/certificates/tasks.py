import csv
import io
import zipfile

from celery import shared_task
from django.core.files.base import ContentFile

from base.enums import StorageProvider
from modules.certificates.choices import (
    CertificateBatchStatusChoices,
    ExportStatusChoices,
)
from modules.certificates.models import CertificateBatch
from modules.certificates.rendering import (
    generate_certificate_for_row,
    load_prepared_image_box,
)
from modules.core.models import UploadFile


def _read_rows(batch):
    with batch.file.open() as fh:
        yield from csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8-sig"))



@shared_task
def add(a, b):
    return a + b

@shared_task
def generate_certificates_task(batch_uuid):
    batch = CertificateBatch.objects.get(uuid=batch_uuid)
    batch.generation_status = CertificateBatchStatusChoices.PROCESSING
    batch.save(update_fields=["generation_status"])

    boxes = list(batch.template.boxes.all())

    try:
        image_boxes = [load_prepared_image_box(image_box) for image_box in batch.template.image_boxes.all()]

        batch.total_rows = sum(1 for _ in _read_rows(batch))
        batch.save(update_fields=["total_rows"])

        for index, row in enumerate(_read_rows(batch)):
            generate_certificate_for_row(batch, index, row, boxes, image_boxes)
            batch.processed_rows = index + 1
            batch.save(update_fields=["processed_rows"])

        batch.generation_status = CertificateBatchStatusChoices.COMPLETED
        batch.save(update_fields=["generation_status"])
    except Exception as exc:
        batch.generation_status = CertificateBatchStatusChoices.FAILED
        batch.error_message = str(exc)
        batch.save(update_fields=["generation_status", "error_message"])


@shared_task
def export_certificates_task(batch_uuid):
    batch = CertificateBatch.objects.get(uuid=batch_uuid)
    batch.export_status = ExportStatusChoices.PROCESSING
    batch.save(update_fields=["export_status"])

    try:
        certificates = list(batch.certificates.order_by("row_index"))
        buffer = io.BytesIO()
        failures = []

        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for cert in certificates:
                if cert.is_successful and cert.file:
                    with cert.file.open() as fh:
                        zf.writestr(cert.file.original_filename, fh.read())
                else:
                    failures.append(cert)
                batch.export_processed_rows += 1
                batch.save(update_fields=["export_processed_rows"])

            if failures:
                report = io.StringIO()
                writer = csv.writer(report)
                writer.writerow(["row_index", "error_message"])
                for cert in failures:
                    writer.writerow([cert.row_index, cert.error_message])
                zf.writestr("errors.csv", report.getvalue())

        zip_upload = UploadFile.upload(
            ContentFile(buffer.getvalue(), name=f"batch_{batch.template.name.lower()}_export.zip"),
            provider=StorageProvider.S3,
        )
        batch.export_file = zip_upload
        batch.export_status = ExportStatusChoices.COMPLETED
        batch.save(update_fields=["export_file", "export_status"])
    except Exception as exc:
        batch.export_status = ExportStatusChoices.FAILED
        batch.export_error_message = str(exc)
        batch.save(update_fields=["export_status", "export_error_message"])
