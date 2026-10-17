from unittest import mock

from django.test import TestCase
from rest_framework.test import APIClient

from modules.core.models import User

VERIFY = "google.oauth2.id_token.verify_oauth2_token"
CLAIMS = {"sub": "g-1", "email": "ravi@example.com", "email_verified": True, "name": "Ravi"}


class RegisterTests(TestCase):
    def test_register_logs_in(self):
        client = APIClient()
        r = client.post("/core/register/", {"username": "asha", "name": "Asha", "email": "asha@example.com", "password": "Sturdy-pass-91"}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertNotIn("password", r.json())
        self.assertEqual(client.get("/core/login/").json()["username"], "asha")

    def test_register_rejects_taken_email_and_weak_password(self):
        User.objects.create_user("asha", "asha@example.com", "Sturdy-pass-91")
        r = APIClient().post("/core/register/", {"username": "asha2", "name": "A", "email": "ASHA@example.com", "password": "123"}, format="json")
        self.assertEqual(r.status_code, 418)
        self.assertIn("email", r.json())
        self.assertIn("password", r.json())


class GoogleAuthTests(TestCase):
    def post(self, claims=CLAIMS, **patch):
        with mock.patch(VERIFY, return_value=claims, **patch):
            return APIClient().post("/core/google/", {"id_token": "x"}, format="json")

    def test_creates_then_reuses_user(self):
        self.assertEqual(self.post().status_code, 201)
        self.assertEqual(self.post().status_code, 201)
        user = User.objects.get(google_id="g-1")
        self.assertEqual(user.username, "ravi")
        self.assertFalse(user.has_usable_password())

    def test_does_not_link_existing_password_account(self):
        User.objects.create_user("ravi", "ravi@example.com", "Sturdy-pass-91")
        self.assertEqual(self.post().status_code, 409)

    def test_rejects_unverified_or_invalid_token(self):
        self.assertEqual(self.post({**CLAIMS, "email_verified": False}).status_code, 418)
        self.assertEqual(self.post(side_effect=ValueError("bad")).status_code, 418)
