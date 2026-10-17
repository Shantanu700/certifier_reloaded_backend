import io
from datetime import timedelta
from unittest import mock

import requests
from cryptography.fernet import Fernet
from django.test import TestCase, override_settings
from django.utils import timezone
from PIL import Image

from modules.canva import services
from modules.canva.models import CanvaConnection
from modules.canva.services import CanvaError, CanvaService
from modules.core.models import User

CANVA_SETTINGS = {
    "CANVA_CLIENT_ID": "client",
    "CANVA_CLIENT_SECRET": "secret",
    "CANVA_REDIRECT_URI": "http://127.0.0.1:3000/canva/callback",
    "CANVA_TOKEN_KEY": Fernet.generate_key().decode(),
}


def fake_response(status=200, body=None, content=b""):
    response = mock.Mock(status_code=status, ok=status < 400)
    response.json.return_value = body or {}
    response.iter_content.return_value = [content]
    response.__enter__ = mock.Mock(return_value=response)
    response.__exit__ = mock.Mock(return_value=False)
    return response


def png_bytes(size=(40, 30)):
    buffer = io.BytesIO()
    Image.new("RGB", size, "white").save(buffer, "PNG")
    return buffer.getvalue()


@override_settings(**CANVA_SETTINGS)
class CanvaServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="canva-test", password=None)
        self.service = CanvaService(self.user)
        self.service.http = mock.Mock()

    def connect_with(self, expires_in_seconds):
        CanvaConnection.objects.create(
            user=self.user,
            access_token_enc=self.service._encrypt("access-1"),
            refresh_token_enc=self.service._encrypt("refresh-1"),
            expires_at=timezone.now() + timedelta(seconds=expires_in_seconds),
        )

    def stored_refresh_token(self):
        return self.service._decrypt(CanvaConnection.objects.get(user=self.user).refresh_token_enc)

    # --- OAuth / token rotation ----------------------------------------------

    def test_connect_rejects_mismatched_state(self):
        session = {}
        self.service.authorize_url(session)
        with self.assertRaises(CanvaError):
            self.service.connect("code", "forged-state", session)
        self.service.http.post.assert_not_called()
        self.assertFalse(self.service.is_connected())

    def test_expired_access_token_is_refreshed_and_rotated_refresh_token_saved(self):
        self.connect_with(expires_in_seconds=-10)
        self.service.http.post.return_value = fake_response(
            body={"access_token": "access-2", "refresh_token": "refresh-2", "expires_in": 14400}
        )

        self.assertEqual(self.service._access_token(), "access-2")
        self.assertEqual(self.service.http.post.call_args.kwargs["data"]["refresh_token"], "refresh-1")
        self.assertEqual(self.stored_refresh_token(), "refresh-2")

    def test_network_failure_during_refresh_keeps_stored_tokens(self):
        self.connect_with(expires_in_seconds=-10)
        self.service.http.post.side_effect = requests.ConnectionError

        with self.assertRaises(CanvaError):
            self.service._access_token()
        self.assertEqual(self.stored_refresh_token(), "refresh-1")

    def test_rejected_refresh_token_disconnects(self):
        self.connect_with(expires_in_seconds=-10)
        self.service.http.post.return_value = fake_response(status=400, body={"error": "invalid_grant"})

        with self.assertRaisesMessage(CanvaError, services.RECONNECT_MESSAGE):
            self.service._access_token()
        self.assertFalse(self.service.is_connected())

    # --- Export polling ------------------------------------------------------

    @mock.patch.object(services.time, "sleep")
    def test_export_polls_until_success_and_returns_png(self, _sleep):
        self.connect_with(expires_in_seconds=3600)
        self.service.http.request.side_effect = [
            fake_response(body={"job": {"id": "job-1", "status": "in_progress"}}),
            fake_response(body={"job": {"id": "job-1", "status": "success", "urls": ["https://export-download.canva.com/x"]}}),
        ]
        self.service.http.get.return_value = fake_response(content=png_bytes((40, 30)))

        image = self.service.export_design("DAF123", page=2)

        create_call = self.service.http.request.call_args_list[0]
        self.assertEqual(create_call.kwargs["json"]["format"]["pages"], [2])
        self.assertEqual((image.width, image.height, image.content_type), (40, 30, "image/png"))
        self.assertTrue(image.filename.endswith(".png"))

    def test_failed_export_surfaces_canva_reason(self):
        self.connect_with(expires_in_seconds=3600)
        self.service.http.request.return_value = fake_response(
            body={"job": {"id": "job-1", "status": "failed", "error": {"code": "license_required", "message": "x"}}}
        )
        with self.assertRaisesMessage(CanvaError, services.ERROR_MESSAGES["license_required"]):
            self.service.export_design("DAF123", page=1)

    @mock.patch.object(services, "EXPORT_TIMEOUT", -1)
    def test_export_times_out(self):
        self.connect_with(expires_in_seconds=3600)
        self.service.http.request.return_value = fake_response(body={"job": {"id": "job-1", "status": "in_progress"}})
        with self.assertRaisesMessage(CanvaError, "taking too long"):
            self.service.export_design("DAF123", page=1)
