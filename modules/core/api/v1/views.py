import base64
from typing import Any

import uuid6
from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiParameter,
    extend_schema,
)
from rest_framework import status
from rest_framework.response import Response

from base.decorators import extend_response
from base.enums import RoleEnum, StorageProvider
from base.exceptions import SerializerError
from base.storage import StorageError, get_storage_service
from base.throttles import AddressRateThrottle, LoginRateThrottle
from base.types import DefaultSuccessResponseType
from base.views import BaseAV
from modules.core.api.v1.serializers import (
    GoogleAuthSerializer,
    LoginSerializer,
    PresignUploadRequestSerializer,
    PresignUploadResponseSerializer,
    ProfileSerializer,
    RegisterSerializer,
    VerifyEmailSerializer,
)
from modules.core.models import UploadFile, User
from modules.core.services import EmailVerificationError, EmailVerificationService

# Write your views here


class LoginAV(BaseAV):
    "Login/Logout API View"

    authentication = {
        "post": False,
    }
    throttle_classes = [LoginRateThrottle]

    def decrypt_auth(self, meta_info) -> dict[str, Any]:
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
        request=None,
        parameters=[
            OpenApiParameter(
                name="Authorization",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.HEADER,
                required=True,
                examples=[
                    OpenApiExample(
                        name="User Authentication",
                        value="Basic ZGVtb19pc3N1ZXI6eW91ci1wYXNzd29yZA==",  # demo_issuer:your-password
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

    @extend_response(DefaultSuccessResponseType)
    def delete(self, request):
        logout(request)
        response = {
            "msg": "Logout successful.",
        }
        return Response(response, status=status.HTTP_200_OK)


class RegisterAV(BaseAV):
    "Sign Up API View"

    authentication = {
        "post": False,
    }
    throttle_classes = [AddressRateThrottle]
    throttle_scope = "signup"

    @extend_schema(request=RegisterSerializer)
    @extend_response(LoginSerializer())
    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        if not serializer.is_valid():
            raise SerializerError(serializer.errors)
        user = serializer.save()
        try:
            EmailVerificationService(user).send()
        except EmailVerificationError:
            pass  # the account still works; the profile page offers a resend
        login(request, user)
        return LoginSerializer(instance=user).data


def _generate_username_from_email(email: str) -> str:
    base = email.split("@")[0][:150]
    username = base
    suffix = 0
    while User.objects.filter(username=username).exists():
        suffix += 1
        username = f"{base[:150 - len(str(suffix)) - 1]}-{suffix}"
    return username


class GoogleAuthAV(BaseAV):
    "Google Login/Sign Up API View"

    authentication = {
        "post": False,
    }

    @extend_schema(request=GoogleAuthSerializer)
    @extend_response(LoginSerializer())
    def post(self, request):
        serializer = GoogleAuthSerializer(data=request.data)
        if not serializer.is_valid():
            raise SerializerError(serializer.errors)
        claims = serializer.validated_data["id_token"]

        user = User.objects.filter(google_id=claims["sub"]).first()
        if user is None:
            # Registration doesn't verify email ownership, so linking Google to an
            # existing account by email would let whoever registered that address first
            # keep password access to the Google user's account.
            if User.objects.filter(email__iexact=claims["email"]).exists():
                self.fail("An account with this email already exists. Log in with your username and password.")
            if "role" not in serializer.validated_data:
                self.fail("No account yet. Sign up first and choose Issuer or Recipient.")
            user = User.objects.create_user(
                username=_generate_username_from_email(claims["email"]),
                email=claims["email"],
                name=claims.get("name", ""),
                password=None,
                google_id=claims["sub"],
                role=serializer.validated_data["role"],
                email_verified=True,
            )
        elif not user.email_verified:
            # Google only signs in verified addresses (checked in the serializer).
            user.email_verified = True
            user.save(update_fields=["email_verified"])

        login(request, user)
        return LoginSerializer(instance=user).data


class PresignUploadAV(BaseAV):
    "Request a presigned S3 upload URL for a client-direct file upload."

    roles = [RoleEnum.ISSUER]  # every upload category is issuer material

    @extend_schema(request=PresignUploadRequestSerializer)
    @extend_response(PresignUploadResponseSerializer())
    def post(self, request):
        serializer = PresignUploadRequestSerializer(data=request.data)
        if not serializer.is_valid():
            raise SerializerError(serializer.errors)

        filename = serializer.validated_data["filename"]
        content_type = serializer.validated_data["content_type"]
        allowed_types = serializer.validated_data["allowed_types"]

        key = f"uploads/{uuid6.uuid6()}/{filename}"
        pending = UploadFile.objects.create(
            provider=StorageProvider.S3,
            key=key,
            original_filename=filename,
            content_type=content_type,
        )

        try:
            presigned = get_storage_service(StorageProvider.S3).generate_presigned_upload(
                key, content_type, allowed_types, settings.MAX_FILE_SIZE
            )
        except StorageError as exc:
            self.fail(str(exc))

        return {
            "upload_uuid": pending.uuid,
            "url": presigned.url,
            "headers": presigned.headers,
            "expires_in": presigned.expires_in,
        }


class ProfileAV(BaseAV):
    "The logged-in user's own profile; recipients' fields are what issuers match CSV rows against."

    @extend_schema(operation_id="retrieve_profile")
    @extend_response(ProfileSerializer())
    def get(self, request):
        return ProfileSerializer(instance=request.user).data

    @extend_schema(request=ProfileSerializer)
    @extend_response(ProfileSerializer())
    def put(self, request):
        serializer = ProfileSerializer(instance=request.user, data=request.data, partial=True)
        if not serializer.is_valid():
            raise SerializerError(serializer.errors)
        serializer.save()
        return serializer.data


class VerifyEmailAV(BaseAV):
    "Confirm an email address from the emailed link (works whether or not the user is logged in)."

    authentication = {
        "post": False,
    }

    @extend_schema(request=VerifyEmailSerializer)
    @extend_response(DefaultSuccessResponseType)
    def post(self, request):
        serializer = VerifyEmailSerializer(data=request.data)
        if not serializer.is_valid():
            raise SerializerError(serializer.errors)
        try:
            EmailVerificationService.verify(serializer.validated_data["token"])
        except EmailVerificationError as exc:
            self.fail(str(exc))
        return self.success("Email verified.")


class ResendVerificationAV(BaseAV):
    "Email the logged-in user a fresh verification link."

    @extend_schema(request=None)
    @extend_response(DefaultSuccessResponseType)
    def post(self, request):
        try:
            EmailVerificationService(request.user).send()
        except EmailVerificationError as exc:
            self.fail(str(exc))
        return self.success(f"Verification email sent to {request.user.email}.")
