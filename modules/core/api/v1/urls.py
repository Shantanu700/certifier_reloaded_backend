from django.urls import path
from modules.core.api.v1.views import LoginAV
# Write your urls here

urlpatterns = [
    path(
        "login/",
        LoginAV.as_view(),
    ),
]
