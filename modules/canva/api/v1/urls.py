from django.urls import path

from modules.canva.api.v1.views import (
    CanvaCallbackAV,
    CanvaConnectAV,
    CanvaConnectionAV,
    CanvaDesignsAV,
    CanvaStatusAV,
)

# Write your urls here

urlpatterns = [
    path(
        "connect/",
        CanvaConnectAV.as_view(),
    ),
    path(
        "callback/",
        CanvaCallbackAV.as_view(),
    ),
    path(
        "status/",
        CanvaStatusAV.as_view(),
    ),
    path(
        "connection/",
        CanvaConnectionAV.as_view(),
    ),
    path(
        "designs/",
        CanvaDesignsAV.as_view(),
    ),
]
