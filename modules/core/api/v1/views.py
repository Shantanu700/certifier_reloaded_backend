import base64
from typing import Any, Dict

from django.contrib.auth import authenticate, login, logout
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiParameter,
    extend_schema,
)
from rest_framework import status
from rest_framework.response import Response

from modules.core.api.v1.serializers import (
    LoginSerializer,
)
from base.views import BaseAV
from base.decorators import extend_response
# Write your views here


class LoginAV(BaseAV):
    "Login/Logout API View"

    authentication = {
        "post": False,
    }

    def decrypt_auth(self, meta_info) -> Dict[str, Any]:
        header, data = meta_info.split(" ")
        if header != "Basic":
            return {}
        decrypted_auth = base64.b64decode(data).decode("utf-8")
        credentials = decrypted_auth.split(":")
        return {
            "username": credentials[0],
            "password": credentials[1],
        }

    @extend_response(LoginSerializer())
    def get(self, request):
        serializer = LoginSerializer(
            instance=request.user,
        )
        return Response(serializer.data, status=status.HTTP_200_OK)

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="Authorization",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.HEADER,
                required=True,
                examples=[
                    OpenApiExample(
                        name="User Authentication",
                        value="Basic ZXJwQGtpZXQuZWR1OkBlcnA=",
                        summary="base64 encoded credentials are required",
                        description="",
                    )
                ],
            )
        ]
    )
    @extend_response(LoginSerializer())
    def post(self, request):
        auth_data = request.META.get("HTTP_AUTHORIZATION")
        credentials = self.decrypt_auth(auth_data)
        user = authenticate(request, **credentials)
        if user is not None:
            login(request, user)
            response = {
                "msg": "Login Successful.",
            }
            return LoginSerializer(instance=user).data
        response = {
            "msg": "Invalid Credentials.",
        }
        return Response(response, status=status.HTTP_400_BAD_REQUEST)

    def delete(self, request):
        logout(request)
        response = {
            "msg": "Logout successful.",
        }
        return Response(response, status=status.HTTP_200_OK)
