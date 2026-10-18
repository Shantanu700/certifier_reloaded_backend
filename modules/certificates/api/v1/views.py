import csv

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils.decorators import method_decorator
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from PIL import Image, UnidentifiedImageError
from rest_framework.exceptions import NotFound

from base.decorators import extend_response
from base.enums import RoleEnum, Status, StorageProvider
from base.exceptions import CustomError, SerializerError
from base.pagination import DefaultPagination
from base.storage import StorageError, get_storage_service
from base.types import DefaultSuccessResponseType
from base.validators import validate_with_schema
from base.views import BaseAV
from modules.canva.services import EXPORT_WIDTH, CanvaError, CanvaService, ExportedImage
from modules.certificates.api.v1.serializers import (
    ALLOWED_TEMPLATE_TYPES,
    CanvaTemplateImportResultSerializer,
    CanvaTemplateImportSerializer,
    CertificateBatchExportProgressSerializer,
    CertificateBatchMappingSerializer,
    CertificateBatchNamingSerializer,
    CertificateBatchProgressSerializer,
    CertificateBatchSerializer,
    CertificateBatchUploadSerializer,
    CertificateMatchSerializer,
    CertificateTemplateSerializer,
    CertificateUpdateSerializer,
    CertificateTemplateUpdateSerializer,
    CertificateTemplateUploadSerializer,
    GeneratedCertificateSerializer,
    IssuanceCountsSerializer,
    IssueOneSerializer,
    ReceivedCertificateSerializer,
    TemplateBoxCreateSerializer,
    TemplateBoxSerializer,
    TemplateBoxUpdateSerializer,
    TemplateImageBoxCreateSerializer,
    TemplateImageBoxSerializer,
    TemplateImageBoxUpdateSerializer,
)
from modules.certificates.choices import (
    CertificateBatchStatusChoices,
    ExportStatusChoices,
    IssueStatusChoices,
)
from modules.certificates.models import (
    CertificateBatch,
    CertificateTemplate,
    GeneratedCertificate,
    TemplateBox,
    TemplateImageBox,
)
from modules.certificates.schemas import CSVColumnsSchema
from modules.certificates.services import FILTERS, IssuanceError, IssuanceService
from modules.certificates.tasks import (
    export_certificates_task,
    generate_certificates_task,
)
from modules.core.models import UploadFile

# Write your views here


def confirm_s3_upload(upload_uuid, allowed_types: dict[str, str], max_size: int) -> UploadFile:
    "Look up a pending presigned-upload UploadFile and verify it actually landed in S3; raises CustomError on failure."
    try:
        pending = UploadFile.objects.get(uuid=upload_uuid, provider=StorageProvider.S3)
    except UploadFile.DoesNotExist:
        raise CustomError("Upload reference not found. Request a new presigned upload URL.")

    try:
        result = get_storage_service(StorageProvider.S3).confirm_upload(pending, allowed_types, max_size)
    except StorageError as exc:
        raise CustomError(str(exc))

    pending.content_type = result.content_type
    pending.size = result.size
    pending.save(update_fields=["content_type", "size"])
    return pending


class CertificateTemplateAV(BaseAV):
    "List & Upload Certificate Templates"

    roles = [RoleEnum.ISSUER]

    @extend_schema(
        operation_id="list_certificate_templates",
        parameters=[
            OpenApiParameter("search", OpenApiTypes.STR, description="Case-insensitive match on the template name."),
            OpenApiParameter("page", OpenApiTypes.INT, description="1-indexed page number."),
            OpenApiParameter(
                "page_size",
                OpenApiTypes.INT,
                description=f"Templates per page (default {DefaultPagination.page_size}, max {DefaultPagination.max_page_size}).",
            ),
        ],
    )
    @extend_response(CertificateTemplateSerializer(), paginated=True)
    def get(self, request):
        queryset = CertificateTemplate.objects.filter(owner=request.user).order_by(
            "-created_at"
        )
        search = request.query_params.get("search", "").strip()[:255]
        if search:
            queryset = queryset.filter(name__icontains=search)
        paginator = DefaultPagination()
        page = paginator.paginate_queryset(queryset, request)
        serializer = CertificateTemplateSerializer(
            page, many=True, context={"request": request}
        )
        return paginator.get_paginated_response(serializer.data)

    @extend_schema(request=CertificateTemplateUploadSerializer)
    @extend_response(CertificateTemplateSerializer())
    def post(self, request):
        serializer = CertificateTemplateUploadSerializer(data=request.data)
        if not serializer.is_valid():
            raise SerializerError(serializer.errors)

        upload = confirm_s3_upload(serializer.validated_data["upload"], ALLOWED_TEMPLATE_TYPES, settings.MAX_FILE_SIZE)
        template = CertificateTemplate.objects.create(
            name=serializer.validated_data["name"],
            file=upload,
            owner=request.user,
        )
        return CertificateTemplateSerializer(
            instance=template, context={"request": request}
        ).data


class CertificateTemplateDetailAV(BaseAV):
    "Retrieve, Update & Delete a single Certificate Template"

    roles = [RoleEnum.ISSUER]

    def get_object(self, request, uuid):
        try:
            return CertificateTemplate.objects.get(uuid=uuid, owner=request.user)
        except CertificateTemplate.DoesNotExist:
            raise NotFound("Certificate template not found.")

    @extend_schema(operation_id="retrieve_certificate_template")
    @extend_response(CertificateTemplateSerializer())
    def get(self, request, uuid):
        return CertificateTemplateSerializer(
            instance=self.get_object(request, uuid), context={"request": request}
        ).data

    @extend_schema(request=CertificateTemplateUpdateSerializer)
    @extend_response(CertificateTemplateSerializer())
    def put(self, request, uuid):
        template = self.get_object(request, uuid)
        serializer = CertificateTemplateUpdateSerializer(data=request.data)
        if not serializer.is_valid():
            raise SerializerError(serializer.errors)

        if "name" in serializer.validated_data:
            template.name = serializer.validated_data["name"]

        if "upload" in serializer.validated_data:
            previous_upload = template.file
            template.file = confirm_s3_upload(
                serializer.validated_data["upload"], ALLOWED_TEMPLATE_TYPES, settings.MAX_FILE_SIZE
            )
            previous_upload.status = Status.DELETED
            previous_upload.save()

        template.save()
        return CertificateTemplateSerializer(
            instance=template, context={"request": request}
        ).data

    @extend_response(DefaultSuccessResponseType)
    def delete(self, request, uuid):
        template = self.get_object(request, uuid)
        if GeneratedCertificate.objects.filter(
            batch__template=template, issue_status=IssueStatusChoices.ISSUED
        ).exists():
            self.fail("Recipients hold certificates from this template. Revoke them before deleting it.")
        template.status = Status.DELETED
        template.save()
        return self.success("Certificate template deleted.")


def store_canva_export(image: ExportedImage) -> UploadFile:
    "Persist a Canva export the same place client uploads go (S3). The image was already validated by CanvaService."
    file = ContentFile(image.content, name=image.filename)
    file.content_type = image.content_type
    try:
        return UploadFile.upload(file, provider=StorageProvider.S3)
    except StorageError as exc:
        raise CustomError(str(exc))


@method_decorator(transaction.non_atomic_requests, name="dispatch")
class CertificateTemplateCanvaImportAV(BaseAV):
    "Import one page of a Canva design as a new Certificate Template"

    roles = [RoleEnum.ISSUER]

    @extend_schema(request=CanvaTemplateImportSerializer)
    @extend_response(CanvaTemplateImportResultSerializer())
    def post(self, request):
        serializer = CanvaTemplateImportSerializer(data=request.data)
        if not serializer.is_valid():
            raise SerializerError(serializer.errors)
        data = serializer.validated_data

        try:
            image = CanvaService(request.user).export_design(data["design_id"], data["page"], width=data["width"])
        except CanvaError as exc:
            self.fail(str(exc))

        with transaction.atomic():
            template = CertificateTemplate.objects.create(
                name=data["name"],
                file=store_canva_export(image),
                owner=request.user,
                canva_design_id=data["design_id"],
                canva_page=data["page"],
            )
        return {
            "template": CertificateTemplateSerializer(instance=template, context={"request": request}).data,
            "width": image.width,
            "height": image.height,
            "downscaled": image.downscaled,
        }


@method_decorator(transaction.non_atomic_requests, name="dispatch")
class CertificateTemplateCanvaSyncAV(BaseAV):
    "Re-export a Canva-imported Certificate Template's design page and replace its file"

    roles = [RoleEnum.ISSUER]

    def get_object(self, request, uuid):
        try:
            return CertificateTemplate.objects.get(uuid=uuid, owner=request.user)
        except CertificateTemplate.DoesNotExist:
            raise NotFound("Certificate template not found.")

    @extend_schema(request=None)
    @extend_response(CertificateTemplateSerializer())
    def post(self, request, uuid):
        template = self.get_object(request, uuid)
        if not template.canva_design_id:
            self.fail("This template wasn't imported from Canva.")

        # Box coordinates are image pixels. With boxes placed, re-export at the current width and insist on the
        # same size, or every box would shift. Without boxes, nothing can shift: take the full EXPORT_WIDTH.
        current_size = None
        if template.boxes.exists() or template.image_boxes.exists():
            try:
                with Image.open(template.file.open()) as current:
                    current_size = current.size
            except (StorageError, UnidentifiedImageError, OSError):
                self.fail("Couldn't read the current template file to check its size, so its boxes might move.")

        try:
            image = CanvaService(request.user).export_design(
                template.canva_design_id,
                template.canva_page or 1,
                width=current_size[0] if current_size else EXPORT_WIDTH,
            )
        except CanvaError as exc:
            self.fail(str(exc))

        if current_size and current_size != (image.width, image.height):
            self.fail(
                f"The Canva design now exports at {image.width}×{image.height}px but this template is "
                f"{current_size[0]}×{current_size[1]}px, so its boxes would move. Import it as a new template instead."
            )

        with transaction.atomic():
            previous_upload = template.file
            template.file = store_canva_export(image)
            template.save()
            previous_upload.status = Status.DELETED
            previous_upload.save()
        return CertificateTemplateSerializer(instance=template, context={"request": request}).data


class CertificateTemplateBoxAV(BaseAV):
    "List & Create Text Boxes for a Certificate Template"

    roles = [RoleEnum.ISSUER]

    def get_template(self, request, template_uuid):
        try:
            return CertificateTemplate.objects.get(uuid=template_uuid, owner=request.user)
        except CertificateTemplate.DoesNotExist:
            raise NotFound("Certificate template not found.")

    @extend_schema(operation_id="list_template_boxes")
    @extend_response(TemplateBoxSerializer(), paginated=True)
    def get(self, request, template_uuid):
        template = self.get_template(request, template_uuid)
        boxes = template.boxes.order_by("created_at")
        return TemplateBoxSerializer(boxes, many=True, context={"request": request}).data

    @extend_schema(request=TemplateBoxCreateSerializer)
    @extend_response(TemplateBoxSerializer())
    def post(self, request, template_uuid):
        template = self.get_template(request, template_uuid)
        serializer = TemplateBoxCreateSerializer(data=request.data, context={"template": template})
        if not serializer.is_valid():
            raise SerializerError(serializer.errors)

        box = TemplateBox.objects.create(template=template, **serializer.validated_data)
        return TemplateBoxSerializer(instance=box, context={"request": request}).data


class CertificateTemplateBoxDetailAV(BaseAV):
    "Retrieve, Update & Delete a single Template Box"

    roles = [RoleEnum.ISSUER]

    def get_object(self, request, template_uuid, uuid):
        try:
            return TemplateBox.objects.get(uuid=uuid, template__uuid=template_uuid, template__owner=request.user)
        except TemplateBox.DoesNotExist:
            raise NotFound("Template box not found.")

    @extend_schema(operation_id="retrieve_template_box")
    @extend_response(TemplateBoxSerializer())
    def get(self, request, template_uuid, uuid):
        return TemplateBoxSerializer(
            instance=self.get_object(request, template_uuid, uuid), context={"request": request}
        ).data

    @extend_schema(request=TemplateBoxUpdateSerializer)
    @extend_response(TemplateBoxSerializer())
    def put(self, request, template_uuid, uuid):
        box = self.get_object(request, template_uuid, uuid)
        serializer = TemplateBoxUpdateSerializer(data=request.data, context={"box": box})
        if not serializer.is_valid():
            raise SerializerError(serializer.errors)

        for field, value in serializer.validated_data.items():
            setattr(box, field, value)
        box.save()
        return TemplateBoxSerializer(instance=box, context={"request": request}).data

    @extend_response(DefaultSuccessResponseType)
    def delete(self, request, template_uuid, uuid):
        box = self.get_object(request, template_uuid, uuid)
        box.status = Status.DELETED
        box.save()
        return self.success("Template box deleted.")


class CertificateTemplateImageBoxAV(BaseAV):
    "List & Create Image Boxes for a Certificate Template"

    roles = [RoleEnum.ISSUER]

    def get_template(self, request, template_uuid):
        try:
            return CertificateTemplate.objects.get(uuid=template_uuid, owner=request.user)
        except CertificateTemplate.DoesNotExist:
            raise NotFound("Certificate template not found.")

    @extend_schema(operation_id="list_template_image_boxes")
    @extend_response(TemplateImageBoxSerializer(), paginated=True)
    def get(self, request, template_uuid):
        template = self.get_template(request, template_uuid)
        image_boxes = template.image_boxes.order_by("created_at")
        return TemplateImageBoxSerializer(image_boxes, many=True, context={"request": request}).data

    @extend_schema(request=TemplateImageBoxCreateSerializer)
    @extend_response(TemplateImageBoxSerializer())
    def post(self, request, template_uuid):
        template = self.get_template(request, template_uuid)
        serializer = TemplateImageBoxCreateSerializer(data=request.data, context={"template": template})
        if not serializer.is_valid():
            raise SerializerError(serializer.errors)

        validated = dict(serializer.validated_data)
        upload = confirm_s3_upload(validated.pop("upload"), settings.ALLOWED_IMG_TYPES, settings.MAX_FILE_SIZE)
        image_box = TemplateImageBox.objects.create(template=template, image=upload, **validated)
        return TemplateImageBoxSerializer(instance=image_box, context={"request": request}).data


class CertificateTemplateImageBoxDetailAV(BaseAV):
    "Retrieve, Update & Delete a single Template Image Box"

    roles = [RoleEnum.ISSUER]

    def get_object(self, request, template_uuid, uuid):
        try:
            return TemplateImageBox.objects.get(uuid=uuid, template__uuid=template_uuid, template__owner=request.user)
        except TemplateImageBox.DoesNotExist:
            raise NotFound("Template image box not found.")

    @extend_schema(operation_id="retrieve_template_image_box")
    @extend_response(TemplateImageBoxSerializer())
    def get(self, request, template_uuid, uuid):
        return TemplateImageBoxSerializer(
            instance=self.get_object(request, template_uuid, uuid), context={"request": request}
        ).data

    @extend_schema(request=TemplateImageBoxUpdateSerializer)
    @extend_response(TemplateImageBoxSerializer())
    def put(self, request, template_uuid, uuid):
        image_box = self.get_object(request, template_uuid, uuid)
        serializer = TemplateImageBoxUpdateSerializer(data=request.data, context={"image_box": image_box})
        if not serializer.is_valid():
            raise SerializerError(serializer.errors)

        validated = dict(serializer.validated_data)
        if "upload" in validated:
            previous_upload = image_box.image
            image_box.image = confirm_s3_upload(
                validated.pop("upload"), settings.ALLOWED_IMG_TYPES, settings.MAX_FILE_SIZE
            )
            previous_upload.status = Status.DELETED
            previous_upload.save()

        for field, value in validated.items():
            setattr(image_box, field, value)
        image_box.save()
        return TemplateImageBoxSerializer(instance=image_box, context={"request": request}).data

    @extend_response(DefaultSuccessResponseType)
    def delete(self, request, template_uuid, uuid):
        image_box = self.get_object(request, template_uuid, uuid)
        image_box.status = Status.DELETED
        image_box.save()
        return self.success("Template image box deleted.")


class CertificateBatchAV(BaseAV):
    "List & Upload Certificate Batches (CSV) for a Certificate Template"

    roles = [RoleEnum.ISSUER]

    def get_template(self, request, template_uuid):
        try:
            return CertificateTemplate.objects.get(uuid=template_uuid, owner=request.user)
        except CertificateTemplate.DoesNotExist:
            raise NotFound("Certificate template not found.")

    @extend_schema(operation_id="list_certificate_batches")
    @extend_response(CertificateBatchSerializer(), paginated=True)
    def get(self, request, template_uuid):
        template = self.get_template(request, template_uuid)
        queryset = template.batches.filter(owner=request.user).order_by("-created_at")
        paginator = DefaultPagination()
        page = paginator.paginate_queryset(queryset, request)
        serializer = CertificateBatchSerializer(page, many=True, context={"request": request})
        return paginator.get_paginated_response(serializer.data)

    @extend_schema(request=CertificateBatchUploadSerializer)
    @extend_response(CertificateBatchSerializer())
    def post(self, request, template_uuid):
        template = self.get_template(request, template_uuid)
        serializer = CertificateBatchUploadSerializer(data=request.data)
        if not serializer.is_valid():
            raise SerializerError(serializer.errors)

        upload = confirm_s3_upload(serializer.validated_data["upload"], settings.ALLOWED_CSV_TYPES, settings.MAX_FILE_SIZE)

        try:
            with upload.open() as fh:
                first_line = fh.readline().decode("utf-8-sig")
        except StorageError as exc:
            self.fail(str(exc))
        csv_columns = next(csv.reader([first_line]), [])
        validate_with_schema(CSVColumnsSchema, csv_columns)

        batch = CertificateBatch.objects.create(
            template=template,
            owner=request.user,
            file=upload,
            csv_columns=csv_columns,
        )
        return CertificateBatchSerializer(instance=batch, context={"request": request}).data


class CertificateBatchDetailAV(BaseAV):
    "Retrieve & Delete a single Certificate Batch"

    roles = [RoleEnum.ISSUER]

    def get_object(self, request, uuid):
        try:
            return CertificateBatch.objects.get(uuid=uuid, owner=request.user)
        except CertificateBatch.DoesNotExist:
            raise NotFound("Certificate batch not found.")

    @extend_schema(operation_id="retrieve_certificate_batch")
    @extend_response(CertificateBatchSerializer())
    def get(self, request, uuid):
        return CertificateBatchSerializer(instance=self.get_object(request, uuid), context={"request": request}).data

    @extend_response(DefaultSuccessResponseType)
    def delete(self, request, uuid):
        batch = self.get_object(request, uuid)
        if batch.certificates.filter(issue_status=IssueStatusChoices.ISSUED).exists():
            self.fail("Recipients hold certificates from this batch. Revoke them before deleting it.")
        batch.status = Status.DELETED
        batch.save()
        return self.success("Certificate batch deleted.")


class CertificateBatchProgressAV(BaseAV):
    "Lightweight polling endpoint: generation_status/total_rows/processed_rows only, nothing else."

    roles = [RoleEnum.ISSUER]

    def get_object(self, request, uuid):
        try:
            return CertificateBatch.objects.get(uuid=uuid, owner=request.user)
        except CertificateBatch.DoesNotExist:
            raise NotFound("Certificate batch not found.")

    @extend_schema(operation_id="retrieve_certificate_batch_progress")
    @extend_response(CertificateBatchProgressSerializer())
    def get(self, request, uuid):
        return CertificateBatchProgressSerializer(instance=self.get_object(request, uuid)).data


class CertificateBatchMappingAV(BaseAV):
    "Set the CSV column -> template box mapping for a Certificate Batch"

    roles = [RoleEnum.ISSUER]

    def get_object(self, request, uuid):
        try:
            return CertificateBatch.objects.get(uuid=uuid, owner=request.user)
        except CertificateBatch.DoesNotExist:
            raise NotFound("Certificate batch not found.")

    @extend_schema(request=CertificateBatchMappingSerializer)
    @extend_response(CertificateBatchSerializer())
    def put(self, request, uuid):
        batch = self.get_object(request, uuid)
        if batch.is_manual:
            self.fail("Certificates issued by hand have no CSV to map.")
        serializer = CertificateBatchMappingSerializer(data=request.data)
        if not serializer.is_valid():
            raise SerializerError(serializer.errors)

        mapping = serializer.validated_data["mapping"]

        box_keys = set(batch.template.boxes.values_list("key", flat=True))
        unknown_boxes = set(mapping) - box_keys
        if unknown_boxes:
            self.fail(f"Unknown box key(s): {', '.join(sorted(unknown_boxes))}.")

        unknown_columns = set(mapping.values()) - set(batch.csv_columns)
        if unknown_columns:
            self.fail(f"Unknown CSV column(s): {', '.join(sorted(unknown_columns))}.")

        batch.mapping = mapping
        batch.generation_status = CertificateBatchStatusChoices.MAPPED
        batch.save()
        return CertificateBatchSerializer(instance=batch, context={"request": request}).data


class CertificateBatchNamingAV(BaseAV):
    "Set the filename-naming convention for a Certificate Batch"

    roles = [RoleEnum.ISSUER]

    def get_object(self, request, uuid):
        try:
            return CertificateBatch.objects.get(uuid=uuid, owner=request.user)
        except CertificateBatch.DoesNotExist:
            raise NotFound("Certificate batch not found.")

    @extend_schema(request=CertificateBatchNamingSerializer)
    @extend_response(CertificateBatchSerializer())
    def put(self, request, uuid):
        batch = self.get_object(request, uuid)
        if batch.is_manual:
            self.fail("Certificates issued by hand have no CSV to name files from.")
        serializer = CertificateBatchNamingSerializer(data=request.data)
        if not serializer.is_valid():
            raise SerializerError(serializer.errors)

        naming_convention = serializer.validated_data["naming_convention"]
        if naming_convention is not None:
            csv_keys = {part["key"] for part in naming_convention if part.get("type") == "csv_key"}
            unknown_columns = csv_keys - set(batch.csv_columns)
            if unknown_columns:
                self.fail(f"Unknown CSV column(s): {', '.join(sorted(unknown_columns))}.")

        batch.naming_convention = naming_convention
        batch.save()
        return CertificateBatchSerializer(instance=batch, context={"request": request}).data


class CertificateBatchGenerateAV(BaseAV):
    "Enqueue certificate generation for a mapped Certificate Batch"

    roles = [RoleEnum.ISSUER]

    def get_object(self, request, uuid):
        try:
            return CertificateBatch.objects.get(uuid=uuid, owner=request.user)
        except CertificateBatch.DoesNotExist:
            raise NotFound("Certificate batch not found.")

    @extend_schema(request=None)
    @extend_response(CertificateBatchSerializer())
    def post(self, request, uuid):
        batch = self.get_object(request, uuid)
        allowed_statuses = (
            CertificateBatchStatusChoices.MAPPED,
            CertificateBatchStatusChoices.COMPLETED,
            CertificateBatchStatusChoices.FAILED,
        )
        if batch.is_manual:
            self.fail("Certificates issued by hand are generated one at a time.")
        if batch.generation_status not in allowed_statuses:
            self.fail(f"Cannot generate from a batch in '{batch.generation_status}' status.")
        if batch.certificates.filter(issue_status=IssueStatusChoices.ISSUED).exists():
            self.fail("Some certificates from this batch are issued. Revoke them before generating again.")

        # Re-running replaces the drafts instead of duplicating every row; revoked ones stay as the record.
        batch.certificates.filter(issue_status=IssueStatusChoices.DRAFT).update(status=Status.DELETED)
        batch.generation_status = CertificateBatchStatusChoices.QUEUED
        batch.processed_rows = 0
        batch.total_rows = 0
        batch.error_message = ""
        batch.save()

        generate_certificates_task.delay(str(batch.uuid))
        return CertificateBatchSerializer(instance=batch, context={"request": request}).data


class CertificateBatchExportAV(BaseAV):
    "Enqueue building a zip of a Certificate Batch's generated certificates"

    roles = [RoleEnum.ISSUER]

    def get_object(self, request, uuid):
        try:
            return CertificateBatch.objects.get(uuid=uuid, owner=request.user)
        except CertificateBatch.DoesNotExist:
            raise NotFound("Certificate batch not found.")

    @extend_schema(request=None)
    @extend_response(CertificateBatchSerializer())
    def post(self, request, uuid):
        batch = self.get_object(request, uuid)

        if batch.generation_status in (CertificateBatchStatusChoices.QUEUED, CertificateBatchStatusChoices.PROCESSING):
            self.fail("Cannot export while generation is still in progress.")
        if batch.export_status in (ExportStatusChoices.QUEUED, ExportStatusChoices.PROCESSING):
            self.fail(f"Export already in progress for this batch ('{batch.export_status}').")
        if not batch.certificates.exists():
            self.fail("No generated certificates to export yet.")

        batch.export_status = ExportStatusChoices.QUEUED
        batch.export_processed_rows = 0
        batch.export_error_message = ""
        batch.save()

        export_certificates_task.delay(str(batch.uuid))
        return CertificateBatchSerializer(instance=batch, context={"request": request}).data


class CertificateBatchExportProgressAV(BaseAV):
    "Lightweight polling endpoint for export progress, including the zip's download link once ready"

    roles = [RoleEnum.ISSUER]

    def get_object(self, request, uuid):
        try:
            return CertificateBatch.objects.get(uuid=uuid, owner=request.user)
        except CertificateBatch.DoesNotExist:
            raise NotFound("Certificate batch not found.")

    @extend_schema(operation_id="retrieve_certificate_batch_export_progress")
    @extend_response(CertificateBatchExportProgressSerializer())
    def get(self, request, uuid):
        return CertificateBatchExportProgressSerializer(
            instance=self.get_object(request, uuid), context={"request": request}
        ).data


class GeneratedCertificateAV(BaseAV):
    "List Generated Certificates for a Certificate Batch"

    roles = [RoleEnum.ISSUER]

    def get_batch(self, request, uuid):
        try:
            return CertificateBatch.objects.get(uuid=uuid, owner=request.user)
        except CertificateBatch.DoesNotExist:
            raise NotFound("Certificate batch not found.")

    @extend_schema(
        operation_id="list_generated_certificates",
        parameters=[
            OpenApiParameter("filter", OpenApiTypes.STR, enum=list(FILTERS), description="Only one review bucket."),
            OpenApiParameter("page", OpenApiTypes.INT, description="1-indexed page number."),
            OpenApiParameter("page_size", OpenApiTypes.INT, description="Certificates per page."),
        ],
    )
    @extend_response(GeneratedCertificateSerializer(), paginated=True)
    def get(self, request, uuid):
        batch = self.get_batch(request, uuid)
        queryset = batch.certificates.select_related("recipient", "file").order_by("row_index")
        bucket = FILTERS.get(request.query_params.get("filter", ""))
        if bucket is not None:
            queryset = queryset.filter(bucket)
        paginator = DefaultPagination()
        page = paginator.paginate_queryset(queryset, request)
        serializer = GeneratedCertificateSerializer(page, many=True, context={"request": request})
        return paginator.get_paginated_response(serializer.data)


class CertificateBatchIssuanceAV(BaseAV):
    "How many of a batch's certificates are unassigned, ready, issued, revoked or failed"

    roles = [RoleEnum.ISSUER]

    def get_object(self, request, uuid):
        try:
            return CertificateBatch.objects.get(uuid=uuid, owner=request.user)
        except CertificateBatch.DoesNotExist:
            raise NotFound("Certificate batch not found.")

    @extend_schema(operation_id="retrieve_certificate_batch_issuance")
    @extend_response(IssuanceCountsSerializer())
    def get(self, request, uuid):
        return IssuanceService(self.get_object(request, uuid)).counts()


class CertificateBatchMatchAV(BaseAV):
    "Propose a recipient for each draft certificate by matching CSV columns to recipient profile fields"

    roles = [RoleEnum.ISSUER]

    def get_object(self, request, uuid):
        try:
            return CertificateBatch.objects.get(uuid=uuid, owner=request.user)
        except CertificateBatch.DoesNotExist:
            raise NotFound("Certificate batch not found.")

    @extend_schema(request=CertificateMatchSerializer)
    @extend_response(IssuanceCountsSerializer())
    def post(self, request, uuid):
        batch = self.get_object(request, uuid)
        serializer = CertificateMatchSerializer(data=request.data)
        if not serializer.is_valid():
            raise SerializerError(serializer.errors)
        try:
            return IssuanceService(batch).match(**serializer.validated_data)
        except IssuanceError as exc:
            self.fail(str(exc))


class CertificateBatchIssueAV(BaseAV):
    "Issue every draft certificate of a batch that has a recipient"

    roles = [RoleEnum.ISSUER]

    def get_object(self, request, uuid):
        try:
            return CertificateBatch.objects.get(uuid=uuid, owner=request.user)
        except CertificateBatch.DoesNotExist:
            raise NotFound("Certificate batch not found.")

    @extend_schema(request=None)
    @extend_response(IssuanceCountsSerializer())
    def post(self, request, uuid):
        try:
            return IssuanceService(self.get_object(request, uuid)).issue()
        except IssuanceError as exc:
            self.fail(str(exc))


class CertificateBatchRevokeAV(BaseAV):
    "Revoke every issued certificate of a batch"

    roles = [RoleEnum.ISSUER]

    def get_object(self, request, uuid):
        try:
            return CertificateBatch.objects.get(uuid=uuid, owner=request.user)
        except CertificateBatch.DoesNotExist:
            raise NotFound("Certificate batch not found.")

    @extend_schema(request=None)
    @extend_response(IssuanceCountsSerializer())
    def post(self, request, uuid):
        try:
            return IssuanceService(self.get_object(request, uuid)).revoke()
        except IssuanceError as exc:
            self.fail(str(exc))


class GeneratedCertificateDetailAV(BaseAV):
    "Retrieve, edit (recipient and/or box text) & delete one certificate"

    roles = [RoleEnum.ISSUER]

    def get_object(self, request, uuid):
        try:
            return GeneratedCertificate.objects.select_related("batch__template").get(
                uuid=uuid, batch__owner=request.user
            )
        except GeneratedCertificate.DoesNotExist:
            raise NotFound("Certificate not found.")

    @extend_schema(operation_id="retrieve_generated_certificate")
    @extend_response(GeneratedCertificateSerializer())
    def get(self, request, uuid):
        return GeneratedCertificateSerializer(instance=self.get_object(request, uuid), context={"request": request}).data

    @extend_schema(request=CertificateUpdateSerializer)
    @extend_response(GeneratedCertificateSerializer())
    def put(self, request, uuid):
        certificate = self.get_object(request, uuid)
        serializer = CertificateUpdateSerializer(data=request.data)
        if not serializer.is_valid():
            raise SerializerError(serializer.errors)
        service = IssuanceService(certificate.batch)
        try:
            # Values first: assigning requires a rendered certificate, and editing may be what fixes it.
            if "values" in serializer.validated_data:
                service.edit_values(certificate, serializer.validated_data["values"])
            if "recipient" in serializer.validated_data:
                service.assign(certificate, serializer.validated_data["recipient"])
        except IssuanceError as exc:
            self.fail(str(exc))
        return GeneratedCertificateSerializer(instance=certificate, context={"request": request}).data

    @extend_response(DefaultSuccessResponseType)
    def delete(self, request, uuid):
        certificate = self.get_object(request, uuid)
        try:
            IssuanceService(certificate.batch).delete(certificate)
        except IssuanceError as exc:
            self.fail(str(exc))
        return self.success("Certificate deleted.")


class GeneratedCertificateRevokeAV(BaseAV):
    "Revoke one issued certificate"

    roles = [RoleEnum.ISSUER]

    def get_object(self, request, uuid):
        try:
            return GeneratedCertificate.objects.select_related("batch").get(uuid=uuid, batch__owner=request.user)
        except GeneratedCertificate.DoesNotExist:
            raise NotFound("Certificate not found.")

    @extend_schema(request=None)
    @extend_response(GeneratedCertificateSerializer())
    def post(self, request, uuid):
        certificate = self.get_object(request, uuid)
        try:
            IssuanceService(certificate.batch).revoke_one(certificate)
        except IssuanceError as exc:
            self.fail(str(exc))
        return GeneratedCertificateSerializer(instance=certificate, context={"request": request}).data


class CertificateTemplateIssueOneAV(BaseAV):
    "Render one certificate from typed values and issue it to a recipient (no CSV)"

    roles = [RoleEnum.ISSUER]

    def get_object(self, request, uuid):
        try:
            return CertificateTemplate.objects.get(uuid=uuid, owner=request.user)
        except CertificateTemplate.DoesNotExist:
            raise NotFound("Certificate template not found.")

    @extend_schema(request=IssueOneSerializer)
    @extend_response(GeneratedCertificateSerializer())
    def post(self, request, uuid):
        template = self.get_object(request, uuid)
        serializer = IssueOneSerializer(data=request.data)
        if not serializer.is_valid():
            raise SerializerError(serializer.errors)
        try:
            certificate = IssuanceService.for_manual(template).issue_one(
                serializer.validated_data["recipient"], serializer.validated_data["values"]
            )
        except IssuanceError as exc:
            self.fail(str(exc))
        return GeneratedCertificateSerializer(instance=certificate, context={"request": request}).data


class ReceivedCertificateAV(BaseAV):
    "A recipient's issued and revoked certificates (drafts are never shown)"

    roles = [RoleEnum.RECIPIENT]

    @extend_schema(
        operation_id="list_received_certificates",
        parameters=[
            OpenApiParameter("page", OpenApiTypes.INT, description="1-indexed page number."),
            OpenApiParameter("page_size", OpenApiTypes.INT, description="Certificates per page."),
        ],
    )
    @extend_response(ReceivedCertificateSerializer(), paginated=True)
    def get(self, request):
        queryset = (
            GeneratedCertificate.objects.filter(
                recipient=request.user,
                issue_status__in=(IssueStatusChoices.ISSUED, IssueStatusChoices.REVOKED),
            )
            # Deleting is blocked while certificates are issued, so this only drops revoked ones
            # whose batch or template went later -- and reading those would raise (soft-delete base manager).
            .exclude(batch__status=Status.DELETED)
            .exclude(batch__template__status=Status.DELETED)
            .select_related("batch__template", "batch__owner", "file")
            .order_by("-issued_at")
        )
        paginator = DefaultPagination()
        page = paginator.paginate_queryset(queryset, request)
        serializer = ReceivedCertificateSerializer(page, many=True, context={"request": request})
        return paginator.get_paginated_response(serializer.data)
