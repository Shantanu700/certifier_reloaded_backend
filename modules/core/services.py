from datetime import timedelta
from urllib.parse import urlencode

from django.conf import settings
from django.core import signing
from django.core.cache import cache
from django.core.mail import send_mail

from modules.core.models import User

VERIFY_SALT = "core.email-verification"
VERIFY_MAX_AGE = timedelta(days=3)
RESEND_COOLDOWN_SECONDS = 60


class EmailVerificationError(Exception):
    "Message is safe to show the user."


class EmailVerificationService:
    "Signed, expiring email-verification links. The token binds the user to the address it was sent to."

    def __init__(self, user: User):
        self.user = user

    def send(self):
        if not self.user.email:
            raise EmailVerificationError("Your account has no email address.")
        if self.user.email_verified:
            raise EmailVerificationError("Your email is already verified.")
        # ponytail: per-process cache by default; set CACHES to Redis if several workers serve requests.
        if not cache.add(f"verify-email:{self.user.pk}", 1, RESEND_COOLDOWN_SECONDS):
            raise EmailVerificationError("A verification email was just sent. Wait a minute before asking again.")

        token = signing.dumps({"user": str(self.user.uuid), "email": self.user.email.lower()}, salt=VERIFY_SALT)
        link = f"{settings.FRONTEND_URL}/verify-email?{urlencode({'token': token})}"
        # ponytail: sent inside the request (EMAIL_TIMEOUT caps it); move to a Celery task if SMTP gets slow.
        try:
            send_mail(
                "Confirm your email for Certifier",
                f"Hi {self.user.name or self.user.username},\n\n"
                f"Confirm your email address by opening this link:\n{link}\n\n"
                f"The link works for {VERIFY_MAX_AGE.days} days. If you didn't sign up for Certifier, ignore this email.",
                None,
                [self.user.email],
            )
        except OSError as exc:  # smtplib errors are OSErrors
            cache.delete(f"verify-email:{self.user.pk}")
            raise EmailVerificationError("Couldn't send the verification email. Try again later.") from exc

    @classmethod
    def verify(cls, token: str) -> User:
        try:
            payload = signing.loads(token, salt=VERIFY_SALT, max_age=VERIFY_MAX_AGE)
        except signing.SignatureExpired:
            raise EmailVerificationError("This verification link has expired. Ask for a new one from your profile.")
        except signing.BadSignature:
            raise EmailVerificationError("This verification link isn't valid.")

        user = User.objects.filter(uuid=payload["user"]).first()
        # A link sent to an old address must not verify a new one.
        if user is None or (user.email or "").lower() != payload["email"]:
            raise EmailVerificationError("This verification link isn't valid.")
        if not user.email_verified:
            user.email_verified = True
            user.save(update_fields=["email_verified"])
        return user
