from drf_spectacular.authentication import SessionScheme
from rest_framework.authentication import SessionAuthentication


class ServerSessionAuthentication(SessionAuthentication):
    """Session auth without DRF's CSRF check.

    Only the frontend's server calls the API with a session: it keeps the session id in its own cookie and
    forwards it, sending neither a CSRF token nor an Origin. Browsers can't send our session cookie cross-site
    (SESSION_COOKIE_SAMESITE = "Lax") or read responses cross-origin (no CORS), which is what CSRF guards
    against. Django's CsrfViewMiddleware still protects the non-API views, i.e. the admin.
    """

    def enforce_csrf(self, request):
        return


class ServerSessionScheme(SessionScheme):
    "Documents ServerSessionAuthentication as the session cookie; drf-spectacular only knows the exact base class."

    target_class = "base.authentication.ServerSessionAuthentication"
