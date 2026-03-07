from typing import cast

from rest_framework import status
from rest_framework.permissions import BasePermission
from rest_framework.request import Request

class APIAuthenticationPermission(BasePermission):
    message = "You are unauthenticated! Please login again!"
    code = status.HTTP_401_UNAUTHORIZED

    def has_permission(self, request: Request, view): # type: ignore
        authentication = getattr(view, "authentication", True)
        method = cast(str, request.method).lower()

        if not (
            authentication
            if isinstance(authentication, bool)
            else authentication.get(method, True)
        ):
            return True

        return bool(request.user and request.user.is_authenticated)
