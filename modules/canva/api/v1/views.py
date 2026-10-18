from dataclasses import asdict

from django.db import transaction
from django.utils.decorators import method_decorator
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema

from base.decorators import extend_response
from base.enums import RoleEnum
from base.exceptions import SerializerError
from base.types import DefaultSuccessResponseType
from base.views import BaseAV
from modules.canva.api.v1.serializers import (
    CanvaAuthorizeUrlSerializer,
    CanvaCallbackSerializer,
    CanvaDesignPageSerializer,
    CanvaStatusSerializer,
)
from modules.canva.services import CanvaError, CanvaService

# Write your views here
# Views that may refresh a Canva token run outside ATOMIC_REQUESTS: Canva refresh tokens are single-use,
# so a rotated token must stay committed even if the rest of the request fails (see CanvaService._access_token).


class CanvaConnectAV(BaseAV):
    "Start connecting the user's Canva account"

    roles = [RoleEnum.ISSUER]

    @extend_response(CanvaAuthorizeUrlSerializer())
    def get(self, request):
        try:
            return {"authorize_url": CanvaService(request.user).authorize_url(request.session)}
        except CanvaError as exc:
            self.fail(str(exc))


@method_decorator(transaction.non_atomic_requests, name="dispatch")
class CanvaCallbackAV(BaseAV):
    "Finish connecting: exchange the code Canva redirected back with"

    roles = [RoleEnum.ISSUER]

    @extend_schema(request=CanvaCallbackSerializer)
    @extend_response(DefaultSuccessResponseType)
    def post(self, request):
        serializer = CanvaCallbackSerializer(data=request.data)
        if not serializer.is_valid():
            raise SerializerError(serializer.errors)
        try:
            CanvaService(request.user).connect(
                serializer.validated_data["code"], serializer.validated_data["state"], request.session
            )
        except CanvaError as exc:
            self.fail(str(exc))
        return self.success("Canva connected.")


class CanvaStatusAV(BaseAV):
    "Whether the user has a Canva account connected"

    roles = [RoleEnum.ISSUER]

    @extend_response(CanvaStatusSerializer())
    def get(self, request):
        try:
            return {"connected": CanvaService(request.user).is_connected()}
        except CanvaError as exc:
            self.fail(str(exc))


@method_decorator(transaction.non_atomic_requests, name="dispatch")
class CanvaConnectionAV(BaseAV):
    "Disconnect the user's Canva account"

    roles = [RoleEnum.ISSUER]

    @extend_response(DefaultSuccessResponseType)
    def delete(self, request):
        try:
            CanvaService(request.user).disconnect()
        except CanvaError as exc:
            self.fail(str(exc))
        return self.success("Canva disconnected.")


@method_decorator(transaction.non_atomic_requests, name="dispatch")
class CanvaDesignsAV(BaseAV):
    "List or search the user's Canva designs"

    roles = [RoleEnum.ISSUER]

    @extend_schema(
        parameters=[
            OpenApiParameter("query", OpenApiTypes.STR, description="Search text (max 255 chars)."),
            OpenApiParameter("continuation", OpenApiTypes.STR, description="Token from the previous page."),
        ]
    )
    @extend_response(CanvaDesignPageSerializer())
    def get(self, request):
        try:
            page = CanvaService(request.user).list_designs(
                query=request.query_params.get("query", "")[:255],
                continuation=request.query_params.get("continuation", ""),
            )
        except CanvaError as exc:
            self.fail(str(exc))
        return asdict(page)
