from django.urls import path

from modules.certificates.api.v1.views import (
    GeneratedCertificateDetailAV,
    GeneratedCertificateRevokeAV,
    ReceivedCertificateAV,
    VerifyCertificateAV,
)

urlpatterns = [
    path(
        "",
        ReceivedCertificateAV.as_view(),
    ),
    path(
        "verify/<str:code>/",
        VerifyCertificateAV.as_view(),
    ),
    path(
        "<uuid:uuid>/",
        GeneratedCertificateDetailAV.as_view(),
    ),
    path(
        "<uuid:uuid>/revoke/",
        GeneratedCertificateRevokeAV.as_view(),
    ),
]
