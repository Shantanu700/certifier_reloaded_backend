from django.urls import path

from modules.core.api.v1.views import LoginAV, PresignUploadAV

# Write your urls here

urlpatterns = [
    path(
        "login/",
        LoginAV.as_view(),
    ),
    path(
        "upload-url/",
        PresignUploadAV.as_view(),
    ),
]
