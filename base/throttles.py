import base64
import binascii

from rest_framework.throttling import ScopedRateThrottle, SimpleRateThrottle


class LoginRateThrottle(SimpleRateThrottle):
    """Login attempts per username tried, so guessing one account's password stays slow whichever address
    the guesses come from. Requests without a readable username fall back to the visitor's address."""

    scope = "login"

    def get_cache_key(self, request, view):
        if request.method != "POST":
            return None
        try:
            kind, encoded = request.META.get("HTTP_AUTHORIZATION", "").split(" ", 1)
            username = base64.b64decode(encoded).decode().split(":", 1)[0].lower() if kind == "Basic" else ""
        except (ValueError, binascii.Error, UnicodeDecodeError):
            username = ""
        return self.cache_format % {"scope": self.scope, "ident": username or self.get_ident(request)}


class AddressRateThrottle(ScopedRateThrottle):
    """A view's `throttle_scope` per visitor address, logged in or not. DRF's own keys on the user once logged
    in, and signing up logs the new account in, so every sign-up would start a fresh count."""

    def get_cache_key(self, request, view):
        return self.cache_format % {"scope": self.scope, "ident": self.get_ident(request)}
