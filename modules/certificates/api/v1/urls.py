from django.urls import path

from modules.certificates.api.v1.views import (
    CertificateBatchAV,
    CertificateTemplateAV,
    CertificateTemplateBoxAV,
    CertificateTemplateBoxDetailAV,
    CertificateTemplateDetailAV,
    CertificateTemplateImageBoxAV,
    CertificateTemplateImageBoxDetailAV,
)

# Write your urls here

urlpatterns = [
    path(
        "",
        CertificateTemplateAV.as_view(),
    ),
    path(
        "<uuid:uuid>/",
        CertificateTemplateDetailAV.as_view(),
    ),
    path(
        "<uuid:template_uuid>/boxes/",
        CertificateTemplateBoxAV.as_view(),
    ),
    path(
        "<uuid:template_uuid>/boxes/<uuid:uuid>/",
        CertificateTemplateBoxDetailAV.as_view(),
    ),
    path(
        "<uuid:template_uuid>/image-boxes/",
        CertificateTemplateImageBoxAV.as_view(),
    ),
    path(
        "<uuid:template_uuid>/image-boxes/<uuid:uuid>/",
        CertificateTemplateImageBoxDetailAV.as_view(),
    ),
    path(
        "<uuid:template_uuid>/batches/",
        CertificateBatchAV.as_view(),
    ),
]
