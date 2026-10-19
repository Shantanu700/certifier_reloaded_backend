import base64
from unittest import mock
from urllib.parse import parse_qs, urlparse

from django.core import mail
from django.core.cache import cache
from django.test import Client, TestCase
from rest_framework.test import APIClient

from modules.core.models import User
from modules.core.services import EmailVerificationError, EmailVerificationService

VERIFY = "google.oauth2.id_token.verify_oauth2_token"
CLAIMS = {"sub": "g-1", "email": "ravi@example.com", "email_verified": True, "name": "Ravi"}


class RegisterTests(TestCase):
    def test_register_logs_in(self):
        client = APIClient()
        r = client.post("/core/register/", {"username": "asha", "name": "Asha", "email": "asha@example.com", "password": "Sturdy-pass-91", "role": "recipient"}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertNotIn("password", r.json())
        me = client.get("/core/login/").json()
        self.assertEqual((me["username"], me["role"], me["email_verified"]), ("asha", "recipient", False))
        self.assertEqual(len(mail.outbox), 1)  # verification link

    def test_register_requires_role(self):
        r = APIClient().post("/core/register/", {"username": "asha", "name": "Asha", "email": "asha@example.com", "password": "Sturdy-pass-91"}, format="json")
        self.assertEqual(r.status_code, 418)
        self.assertIn("role", r.json())

    def test_register_rejects_taken_email_and_weak_password(self):
        User.objects.create_user("asha", "asha@example.com", "Sturdy-pass-91")
        r = APIClient().post("/core/register/", {"username": "asha2", "name": "A", "email": "ASHA@example.com", "password": "123"}, format="json")
        self.assertEqual(r.status_code, 418)
        self.assertIn("email", r.json())
        self.assertIn("password", r.json())


class GoogleAuthTests(TestCase):
    def post(self, claims=CLAIMS, role="recipient", **patch):
        with mock.patch(VERIFY, return_value=claims, **patch):
            return APIClient().post("/core/google/", {"id_token": "x", **({"role": role} if role else {})}, format="json")

    def test_creates_then_reuses_user(self):
        self.assertEqual(self.post().status_code, 201)
        self.assertEqual(self.post(role=None).status_code, 201)  # existing account: no role needed
        user = User.objects.get(google_id="g-1")
        self.assertEqual(user.username, "ravi")
        self.assertFalse(user.has_usable_password())
        self.assertEqual(user.role, "recipient")
        self.assertTrue(user.email_verified)

    def test_new_account_needs_role(self):
        self.assertEqual(self.post(role=None).status_code, 409)
        self.assertFalse(User.objects.exists())

    def test_does_not_link_existing_password_account(self):
        User.objects.create_user("ravi", "ravi@example.com", "Sturdy-pass-91")
        self.assertEqual(self.post().status_code, 409)

    def test_rejects_unverified_or_invalid_token(self):
        self.assertEqual(self.post({**CLAIMS, "email_verified": False}).status_code, 418)
        self.assertEqual(self.post(side_effect=ValueError("bad")).status_code, 418)


class EmailVerificationTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user("asha", "asha@example.com", "Sturdy-pass-91", role="recipient")

    def token(self):
        EmailVerificationService(self.user).send()
        link = next(line for line in mail.outbox[-1].body.splitlines() if "token=" in line)
        return parse_qs(urlparse(link).query)["token"][0]

    def test_link_verifies_once_sent(self):
        r = APIClient().post("/core/verify-email/", {"token": self.token()}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        self.user.refresh_from_db()
        self.assertTrue(self.user.email_verified)

    def test_link_rejected_after_email_change_or_tampering(self):
        token = self.token()
        self.assertEqual(APIClient().post("/core/verify-email/", {"token": token + "x"}, format="json").status_code, 409)
        self.user.email = "someone-else@example.com"
        self.user.save()
        with self.assertRaises(EmailVerificationError):
            EmailVerificationService.verify(token)

    def test_resend_is_rate_limited(self):
        self.token()
        with self.assertRaises(EmailVerificationError):
            EmailVerificationService(self.user).send()


class ProfileTests(TestCase):
    def test_phone_is_normalised_and_role_is_read_only(self):
        user = User.objects.create_user("asha", "asha@example.com", "x", role="recipient")
        client = APIClient()
        client.force_authenticate(user)
        r = client.put("/core/profile/", {"phone": "+91 98765-43210", "role": "issuer", "roll_number": "R1"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        user.refresh_from_db()
        self.assertEqual((user.phone, user.role, user.roll_number), ("9876543210", "recipient", "R1"))
        self.assertEqual(client.put("/core/profile/", {"phone": "12345"}, format="json").status_code, 418)


class CrossSiteTests(TestCase):
    "Plain test clients skip CSRF; enforce_csrf_checks=True behaves like a real browser or server caller."

    def setUp(self):
        self.user = User.objects.create_user("asha", "asha@example.com", "Sturdy-pass-91", is_staff=True, is_superuser=True)
        self.client = Client(enforce_csrf_checks=True)

    def login(self):
        basic = base64.b64encode(b"asha:Sturdy-pass-91").decode()
        return self.client.post("/core/login/", HTTP_AUTHORIZATION=f"Basic {basic}")

    def test_session_cookie_is_lax(self):
        r = self.login()
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.cookies["sessionid"]["samesite"], "Lax")

    def test_api_takes_a_session_without_a_csrf_token(self):
        # How the frontend's server calls: the session cookie, no token, no Origin.
        self.login()
        self.assertEqual(self.client.delete("/core/login/").status_code, 200)

    def test_admin_still_needs_a_csrf_token(self):
        r = self.client.post("/admin/login/", {"username": "asha", "password": "Sturdy-pass-91"})
        self.assertEqual(r.status_code, 403)

    def test_no_cors_headers_for_other_sites(self):
        r = self.client.get("/core/login/", HTTP_ORIGIN="https://evil.example")
        self.assertNotIn("Access-Control-Allow-Origin", r.headers)
