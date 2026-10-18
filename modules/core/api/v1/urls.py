from django.urls import path

from modules.core.api.v1.views import (
    GoogleAuthAV,
    LoginAV,
    PresignUploadAV,
    ProfileAV,
    RegisterAV,
    ResendVerificationAV,
    VerifyEmailAV,
)

# Write your urls here

urlpatterns = [
    path(
        "login/",
        LoginAV.as_view(),
    ),
    path(
        "register/",
        RegisterAV.as_view(),
    ),
    path(
        "google/",
        GoogleAuthAV.as_view(),
    ),
    path(
        "upload-url/",
        PresignUploadAV.as_view(),
    ),
    path(
        "profile/",
        ProfileAV.as_view(),
    ),
    path(
        "verify-email/",
        VerifyEmailAV.as_view(),
    ),
    path(
        "verify-email/resend/",
        ResendVerificationAV.as_view(),
    ),
]
