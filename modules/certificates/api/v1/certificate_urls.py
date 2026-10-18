from django.urls import path

from modules.certificates.api.v1.views import (
    GeneratedCertificateDetailAV,
    GeneratedCertificateRevokeAV,
    ReceivedCertificateAV,
)

urlpatterns = [
    path(
        "",
        ReceivedCertificateAV.as_view(),
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
