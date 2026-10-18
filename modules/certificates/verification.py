import secrets
from urllib.parse import urlencode

from django.conf import settings

# Crockford base32: no I, L, O or U, so a code read off paper can't be mistyped into a different one.
ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
CODE_LENGTH = 12  # 60 random bits
LOOKALIKES = str.maketrans({"O": "0", "I": "1", "L": "1"})


def new_code() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(CODE_LENGTH))


def format_code(code: str) -> str:
    return "-".join(code[i : i + 4] for i in range(0, len(code), 4))


def normalise_code(text: str) -> str | None:
    "A typed or scanned code in canonical form, or None when it can't be one."
    code = "".join(text.split()).replace("-", "").upper().translate(LOOKALIKES)
    if len(code) != CODE_LENGTH or any(char not in ALPHABET for char in code):
        return None
    return code


def verify_url(code: str) -> str:
    return f"{settings.FRONTEND_URL}/verify?{urlencode({'code': format_code(code)})}"
