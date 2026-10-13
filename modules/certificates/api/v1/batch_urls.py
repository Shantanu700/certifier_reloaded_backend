from django.urls import path

from modules.certificates.api.v1.views import (
    CertificateBatchDetailAV,
    CertificateBatchExportAV,
    CertificateBatchExportProgressAV,
    CertificateBatchGenerateAV,
    CertificateBatchMappingAV,
    CertificateBatchNamingAV,
    CertificateBatchProgressAV,
    GeneratedCertificateAV,
)

urlpatterns = [
    path(
        "<uuid:uuid>/",
        CertificateBatchDetailAV.as_view(),
    ),
    path(
        "<uuid:uuid>/progress/",
        CertificateBatchProgressAV.as_view(),
    ),
    path(
        "<uuid:uuid>/mapping/",
        CertificateBatchMappingAV.as_view(),
    ),
    path(
        "<uuid:uuid>/naming/",
        CertificateBatchNamingAV.as_view(),
    ),
    path(
        "<uuid:uuid>/generate/",
        CertificateBatchGenerateAV.as_view(),
    ),
    path(
        "<uuid:uuid>/export/",
        CertificateBatchExportAV.as_view(),
    ),
    path(
        "<uuid:uuid>/export/progress/",
        CertificateBatchExportProgressAV.as_view(),
    ),
    path(
        "<uuid:uuid>/certificates/",
        GeneratedCertificateAV.as_view(),
    ),
]
