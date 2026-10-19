import base64
from unittest import mock
from urllib.parse import parse_qs, urlparse

from django.conf import settings
from django.core import mail
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from rest_framework.test import APIClient
from rest_framework.throttling import SimpleRateThrottle

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
        cache.clear()  # login attempts are rate-limited per username
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


def trusting_proxies(n):
    return override_settings(REST_FRAMEWORK={**settings.REST_FRAMEWORK, "NUM_PROXIES": n})


class RateLimitTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()

    def login(self, username, password="wrong"):
        basic = base64.b64encode(f"{username}:{password}".encode()).decode()
        return self.client.post("/core/login/", HTTP_AUTHORIZATION=f"Basic {basic}")

    def verify(self, address):
        return self.client.get("/certificates/verify/AAAA-BBBB-CCCC/", HTTP_X_FORWARDED_FOR=address).status_code

    def test_login_is_limited_per_username(self):
        User.objects.create_user("asha", "asha@example.com", "Sturdy-pass-91")
        self.assertEqual({self.login("asha").status_code for _ in range(10)}, {400})
        blocked = self.login("asha", "Sturdy-pass-91")  # even the right password, once the limit is hit
        self.assertEqual(blocked.status_code, 429)
        self.assertIn("Retry-After", blocked.headers)
        self.assertEqual(self.login("ravi").status_code, 400)  # other accounts are unaffected

    @mock.patch.object(SimpleRateThrottle, "THROTTLE_RATES", {"signup": "2/hour", "verify": "2/min"})
    def test_signup_and_verify_are_limited_per_forwarded_address(self):
        def register(n, address):
            body = {"username": f"user{n}", "name": "U", "email": f"user{n}@example.com", "password": "Sturdy-pass-91", "role": "recipient"}
            return self.client.post("/core/register/", body, format="json", HTTP_X_FORWARDED_FOR=address).status_code

        with trusting_proxies(1):
            self.assertEqual([register(n, "203.0.113.5") for n in range(3)], [201, 201, 429])
            self.assertEqual(register(3, "198.51.100.7"), 201)
            self.assertEqual([self.verify("203.0.113.5") for _ in range(3)], [409, 409, 429])
            self.assertEqual(self.verify("198.51.100.7"), 409)

    @mock.patch.object(SimpleRateThrottle, "THROTTLE_RATES", {"verify": "2/min"})
    def test_forwarded_address_is_ignored_unless_trusted(self):
        with trusting_proxies(0):  # a made-up address per request doesn't dodge the limit
            self.assertEqual([self.verify(f"203.0.113.{n}") for n in range(3)], [409, 409, 429])


class LoginTests(TestCase):
    def setUp(self):
        cache.clear()  # login attempts are rate-limited per username
        # Server errors come back as a 500 response instead of being raised into the test.
        self.client = Client(raise_request_exception=False)

    def login(self, header):
        return self.client.post("/core/login/", **({"HTTP_AUTHORIZATION": header} if header else {}))

    def basic(self, credentials):
        return "Basic " + base64.b64encode(credentials.encode()).decode()

    def test_password_with_a_colon_logs_in(self):
        User.objects.create_user("asha", "asha@example.com", "pa:ss:word-91")
        self.assertEqual(self.login(self.basic("asha:pa:ss:word-91")).status_code, 201)

    def test_missing_or_garbled_credentials_are_a_400(self):
        for header in (None, "Basic", "Basic not-base64!", self.basic("no-colon"), "Bearer abc"):
            with self.subTest(header=header):
                self.assertEqual(self.login(header).status_code, 400)
