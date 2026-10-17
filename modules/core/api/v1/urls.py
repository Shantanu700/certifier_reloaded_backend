from django.urls import path

from modules.core.api.v1.views import GoogleAuthAV, LoginAV, PresignUploadAV, RegisterAV

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
]
