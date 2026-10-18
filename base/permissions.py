from typing import cast

from rest_framework import status
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission
from rest_framework.request import Request


def allowed_roles(view, method: str):
    "The view's `roles` for this method: a list of RoleEnum values, or 'any'."
    roles = getattr(view, "roles", "any")
    return roles.get(method, "any") if isinstance(roles, dict) else roles


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

        if not (request.user and request.user.is_authenticated):
            return False

        roles = allowed_roles(view, method)
        if roles != "any" and request.user.role not in roles:
            # 403, not the 401 above: the user is logged in, just not allowed here.
            raise PermissionDenied(f"This is only available to {' and '.join(r.label.lower() for r in roles)}s.")
        return True
