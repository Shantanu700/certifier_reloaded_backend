import base64
import hashlib
import io
import secrets
import time
from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import urlencode

import requests
from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from PIL import Image, UnidentifiedImageError

from base.enums import Status
from modules.canva.models import CanvaConnection

API_BASE = "https://api.canva.com/rest/v1"
AUTHORIZE_URL = "https://www.canva.com/api/oauth/authorize"
SCOPES = "design:meta:read design:content:read"
SESSION_KEY = "canva_oauth"
TIMEOUT = (5, 30)  # (connect, read) seconds for every outbound call
REFRESH_MARGIN = timedelta(seconds=60)
EXPORT_POLL_INTERVAL = 1
EXPORT_TIMEOUT = 45
MAX_DOWNLOAD_SIZE = 50_000_000  # hard cap while streaming; the PNG -> JPG fallback still has to land under MAX_FILE_SIZE
DESIGNS_PAGE_SIZE = 24
# Canva exports at the design's own 96 DPI size by default (1123x794 for A4), which prints soft.
# 2480px is ~300 DPI across A4 portrait and ~2.2x native for A4 landscape.
EXPORT_WIDTH = 2480
# Failures a smaller native-size export can't fix, so don't spend a second export on them.
NO_NATIVE_RETRY = {"license_required", "approval_required", "permission_denied", "not_found", "too_many_requests", "timeout"}

ERROR_MESSAGES = {
    "license_required": "This design uses premium Canva elements your account isn't licensed to export.",
    "approval_required": "This design needs approval in Canva before it can be exported.",
    "too_many_requests": "Canva is rate-limiting requests. Wait a minute and try again.",
    "not_found": "That Canva design couldn't be found. It may have been deleted or unshared.",
    "permission_denied": "Your Canva account doesn't have access to that design.",
}
RECONNECT_MESSAGE = "Your Canva connection has expired. Connect Canva again."


class CanvaError(Exception):
    "Raised for any Canva failure; the message is safe to show the user. `code` is Canva's error code when there is one."

    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        self.code = code


class CanvaTokenRejected(CanvaError):
    "The token endpoint refused the grant (expired/revoked refresh token, bad code) -- the user must reconnect."


@dataclass
class Design:
    id: str
    title: str
    thumbnail_url: str | None
    page_count: int | None
    updated_at: int | None


@dataclass
class DesignPage:
    items: list[Design]
    continuation: str | None


@dataclass
class ExportedImage:
    content: bytes
    filename: str
    content_type: str
    width: int
    height: int


class CanvaService:
    "Canva Connect API on behalf of one user: OAuth (PKCE), encrypted token storage with rotation, design listing and export."

    def __init__(self, user):
        missing = [
            name
            for name in ("CANVA_CLIENT_ID", "CANVA_CLIENT_SECRET", "CANVA_REDIRECT_URI", "CANVA_TOKEN_KEY")
            if not getattr(settings, name, None)
        ]
        if missing:
            raise CanvaError(f"Canva import isn't configured on the server (missing {', '.join(missing)}).")
        try:
            self.fernet = Fernet(settings.CANVA_TOKEN_KEY)
        except ValueError as exc:
            raise CanvaError("Canva import isn't configured on the server (CANVA_TOKEN_KEY is not a valid Fernet key).") from exc
        self.user = user
        self.http = requests.Session()

    # --- OAuth ---------------------------------------------------------------

    def authorize_url(self, session) -> str:
        "Start the PKCE flow: the verifier and state live only in the server-side Django session."
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        state = secrets.token_urlsafe(32)
        session[SESSION_KEY] = {"state": state, "verifier": verifier}
        params = {
            "response_type": "code",
            "client_id": settings.CANVA_CLIENT_ID,
            "redirect_uri": settings.CANVA_REDIRECT_URI,
            "scope": SCOPES,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "s256",
        }
        return f"{AUTHORIZE_URL}?{urlencode(params)}"

    def connect(self, code: str, state: str, session) -> None:
        pending = session.pop(SESSION_KEY, None)
        if not pending or not secrets.compare_digest(pending["state"], state):
            raise CanvaError("The Canva sign-in link expired or didn't match. Connect Canva again.")
        tokens = self._token_request(
            {
                "grant_type": "authorization_code",
                "code": code,
                "code_verifier": pending["verifier"],
                "redirect_uri": settings.CANVA_REDIRECT_URI,
            }
        )
        with transaction.atomic():
            connection = CanvaConnection.objects.select_for_update().filter(user=self.user).first()
            self._store_tokens(connection or CanvaConnection(user=self.user), tokens)

    def is_connected(self) -> bool:
        return CanvaConnection.objects.filter(user=self.user).exists()

    def disconnect(self) -> None:
        "Revoke at Canva (best effort) and wipe the stored tokens; revoking the refresh token revokes its whole lineage."
        connection = CanvaConnection.objects.filter(user=self.user).first()
        if connection is None:
            return
        try:
            self.http.post(
                f"{API_BASE}/oauth/revoke",
                data={"token": self._decrypt(connection.refresh_token_enc)},
                auth=(settings.CANVA_CLIENT_ID, settings.CANVA_CLIENT_SECRET),
                timeout=TIMEOUT,
            )
        except (requests.RequestException, CanvaError):
            pass  # the local wipe below is what matters to the user
        self._drop(connection)

    # --- Designs -------------------------------------------------------------

    def list_designs(self, query: str = "", continuation: str = "") -> DesignPage:
        params = {"limit": DESIGNS_PAGE_SIZE, "sort_by": "relevance" if query else "modified_descending"}
        if query:
            params["query"] = query
        if continuation:
            params["continuation"] = continuation
        data = self._request("GET", "/designs", params=params)
        return DesignPage(
            items=[
                Design(
                    id=item["id"],
                    title=item.get("title") or "Untitled design",
                    thumbnail_url=(item.get("thumbnail") or {}).get("url"),
                    page_count=item.get("page_count"),
                    updated_at=item.get("updated_at"),
                )
                for item in data.get("items", [])
            ],
            continuation=data.get("continuation"),
        )

    def export_design(self, design_id: str, page: int, width: int | None = EXPORT_WIDTH) -> ExportedImage:
        """
        One page as an image template, `width` pixels wide (aspect ratio kept; None = Canva's native size).
        PNG keeps text crisp; fall back to JPG when the PNG is over MAX_FILE_SIZE.
        """
        size = {"width": width} if width else {}
        try:
            content = self._export(design_id, page, {"type": "png", **size})
        except CanvaError as exc:
            if not size or exc.code in NO_NATIVE_RETRY:
                raise
            # ponytail: assumes a failed sized export is the Free-plan 1.125x upscale cap and settles for
            # Canva's native size rather than failing the import; surface the lower quality if users ask.
            size = {}
            content = self._export(design_id, page, {"type": "png"})
        content_type, extension, pil_format = "image/png", "png", "PNG"
        if len(content) > settings.MAX_FILE_SIZE:
            content = self._export(design_id, page, {"type": "jpg", "quality": 90, **size})
            content_type, extension, pil_format = "image/jpeg", "jpg", "JPEG"
        if len(content) > settings.MAX_FILE_SIZE:
            raise CanvaError(f"This design exports larger than {settings.MAX_FILE_SIZE // 1_000_000} MB even as JPG. Simplify it in Canva and try again.")

        try:
            with Image.open(io.BytesIO(content)) as image:
                if image.format != pil_format:
                    raise CanvaError("Canva returned an unexpected file. Try the import again.")
                width, height = image.size
        except (UnidentifiedImageError, OSError) as exc:
            raise CanvaError("Canva returned a file that isn't a readable image. Try the import again.") from exc

        return ExportedImage(
            content=content,
            filename=f"canva-{design_id}-page-{page}.{extension}",
            content_type=content_type,
            width=width,
            height=height,
        )

    # --- Internals -----------------------------------------------------------

    def _export(self, design_id: str, page: int, export_format: dict) -> bytes:
        job = self._request(
            "POST",
            "/exports",
            json={"design_id": design_id, "format": {**export_format, "pages": [page], "export_quality": "regular"}},
        )["job"]
        # ponytail: polls inside the request (~45s ceiling); move to a Celery task + progress endpoint if exports start timing out.
        deadline = time.monotonic() + EXPORT_TIMEOUT
        while job["status"] == "in_progress":
            if time.monotonic() > deadline:
                raise CanvaError("Canva is taking too long to export this design. Try again in a minute.", code="timeout")
            time.sleep(EXPORT_POLL_INTERVAL)
            job = self._request("GET", f"/exports/{job['id']}")["job"]

        if job["status"] != "success":
            error = job.get("error") or {}
            raise CanvaError(
                ERROR_MESSAGES.get(error.get("code"), f"Canva couldn't export this design: {error.get('message', 'unknown error')}."),
                code=error.get("code"),
            )
        if not job.get("urls"):
            raise CanvaError("Canva didn't return the exported page. Check the page number and try again.")
        return self._download(job["urls"][0])

    def _download(self, url: str) -> bytes:
        "Export URLs are pre-signed; no Authorization header."
        try:
            with self.http.get(url, timeout=TIMEOUT, stream=True) as response:
                response.raise_for_status()
                chunks, size = [], 0
                for chunk in response.iter_content(chunk_size=1 << 16):
                    size += len(chunk)
                    if size > MAX_DOWNLOAD_SIZE:
                        raise CanvaError("The exported design is too large to import.")
                    chunks.append(chunk)
                return b"".join(chunks)
        except requests.RequestException as exc:
            raise CanvaError("Couldn't download the export from Canva. Try again.") from exc

    def _request(self, method: str, path: str, **kwargs) -> dict:
        "Every call to Canva's REST API goes through here: bearer token, timeouts and error mapping."
        token = self._access_token()
        try:
            response = self.http.request(
                method, f"{API_BASE}{path}", headers={"Authorization": f"Bearer {token}"}, timeout=TIMEOUT, **kwargs
            )
        except requests.RequestException as exc:
            raise CanvaError("Couldn't reach Canva. Try again.") from exc

        if response.status_code == 401:
            # Revoked from Canva's side (user removed the app); the stored grant is useless now.
            connection = CanvaConnection.objects.filter(user=self.user).first()
            if connection is not None:
                self._drop(connection)
            raise CanvaError(RECONNECT_MESSAGE)
        if not response.ok:
            raise self._error_from(response)
        return response.json()

    def _access_token(self) -> str:
        """
        Current access token, refreshing it when it is about to expire.
        Canva refresh tokens are single-use, so the row is locked (two tabs must not both spend the same token)
        and the rotated pair is committed immediately -- callers run outside ATOMIC_REQUESTS so a later
        failure in the request can't roll the new refresh token back.
        """
        with transaction.atomic():
            connection = CanvaConnection.objects.select_for_update().filter(user=self.user).first()
            if connection is None:
                raise CanvaError("Connect your Canva account first.")
            if connection.expires_at - REFRESH_MARGIN > timezone.now():
                return self._decrypt(connection.access_token_enc)

            try:
                tokens = self._token_request({"grant_type": "refresh_token", "refresh_token": self._decrypt(connection.refresh_token_enc)})
            except CanvaTokenRejected:
                pass  # drop outside the atomic block, or the raise below would roll the drop back
            else:
                self._store_tokens(connection, tokens)
                return tokens["access_token"]
        self._drop(connection)
        raise CanvaError(RECONNECT_MESSAGE)

    def _token_request(self, data: dict) -> dict:
        try:
            response = self.http.post(
                f"{API_BASE}/oauth/token",
                data=data,
                auth=(settings.CANVA_CLIENT_ID, settings.CANVA_CLIENT_SECRET),
                timeout=TIMEOUT,
            )
        except requests.RequestException as exc:
            raise CanvaError("Couldn't reach Canva. Try again.") from exc
        if response.status_code in (400, 401):
            raise self._error_from(response, CanvaTokenRejected)
        if not response.ok:
            raise self._error_from(response)
        return response.json()

    def _store_tokens(self, connection: CanvaConnection, tokens: dict) -> None:
        connection.access_token_enc = self._encrypt(tokens["access_token"])
        connection.refresh_token_enc = self._encrypt(tokens["refresh_token"])
        connection.expires_at = timezone.now() + timedelta(seconds=int(tokens["expires_in"]))
        connection.scope = tokens.get("scope", "")
        if connection.pk:
            connection.status = Status.UPDATED
        connection.save()

    def _drop(self, connection: CanvaConnection) -> None:
        "Soft-delete per project convention, but blank the tokens: a disconnected grant must not linger in the DB."
        connection.access_token_enc = ""
        connection.refresh_token_enc = ""
        connection.status = Status.DELETED
        connection.save()

    def _encrypt(self, value: str) -> str:
        return self.fernet.encrypt(value.encode()).decode()

    def _decrypt(self, value: str) -> str:
        try:
            return self.fernet.decrypt(value.encode()).decode()
        except InvalidToken as exc:  # CANVA_TOKEN_KEY rotated since this grant was stored
            raise CanvaError(RECONNECT_MESSAGE) from exc

    @staticmethod
    def _error_from(response, error_class: type[CanvaError] = CanvaError) -> CanvaError:
        try:
            body = response.json()
        except ValueError:
            body = {}
        code = body.get("code") or body.get("error")
        message = ERROR_MESSAGES.get(
            code, f"Canva returned an error: {body.get('message') or body.get('error_description') or response.status_code}."
        )
        return error_class(message, code=code)
