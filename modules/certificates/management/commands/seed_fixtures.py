"""Fill the database with illustrative sample data, dated relative to now.

- an issuer login (demo_issuer) owning three certificate templates, drawn here
  with Pillow so no upload, S3 or Canva account is needed;
- 50 verified student logins (student01 to student50), each holding the
  certificates issued to them: everyone has the workshop one, 30 the hackathon
  one and 10 the merit award, and two workshop certificates are revoked so
  that state shows up too.

Everything it creates hangs off these accounts, so --reset replaces only that
and leaves the rest of the database alone. Files go to local storage
(MEDIA_ROOT), never S3 or Cloudinary. All names are made up, emails use
example.com and no phone numbers are set.
"""

import os
import random
import secrets
from datetime import date, timedelta
from io import BytesIO

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from PIL import Image, ImageDraw, ImageFont

from base.enums import RoleEnum, StorageProvider
from modules.certificates.choices import BoxTypeChoices, TextAlignChoices
from modules.certificates.fonts import BUNDLED_FONTS
from modules.certificates.models import (
    CertificateBatch,
    CertificateTemplate,
    GeneratedCertificate,
    TemplateBox,
    TemplateImageBox,
)
from modules.certificates.services import IssuanceService
from modules.core.models import UploadFile, User

ISSUER = "demo_issuer"
STUDENTS = 50
INSTITUTION = "Demo Institute of Technology"

# A4 landscape proportions; box coordinates below are pixels on this canvas.
SIZE = (2000, 1414)
INK = "#1E1E1E"
MUTED = "#5A5A5A"
PAPER = "#FBF8F1"

# name, the line under CERTIFICATE, body lines, accent, an extra text box (key, values), issued days ago, recipients
TEMPLATES = [
    {
        "name": "Python Programming Workshop",
        "kind": "OF COMPLETION",
        "body": ("has successfully completed the four-week", "Python Programming Workshop"),
        "accent": "#1F4E79",
        "extra": None,
        "days_ago": 120,
        "recipients": STUDENTS,
    },
    {
        "name": "Annual Hackathon 2026",
        "kind": "OF PARTICIPATION",
        "body": ("took part in the 36-hour Annual Hackathon 2026", "as a member of the team"),
        "accent": "#6B2C7E",
        "extra": ("team", ["Nebula", "Byte Busters", "Code Monks", "Pixel Pirates", "Null Pointers",
                           "Stack Smashers", "Green Lanterns", "Quantum Quokkas", "Bug Hunters", "Night Owls"]),
        "days_ago": 60,
        "recipients": 30,
    },
    {
        "name": "Academic Excellence Award",
        "kind": "OF MERIT",
        "body": ("is recognised for outstanding academic performance", "in the 2025-26 session, studying"),
        "accent": "#8A5A00",
        "extra": ("programme", ["B.Tech Computer Science", "B.Tech Electronics", "BCA", "MCA", "B.Tech Mechanical"]),
        "days_ago": 14,
        "recipients": 10,
    },
]

FIRST = ["Aarav", "Diya", "Kabir", "Ishita", "Rohan", "Meera", "Arjun", "Sana", "Vihaan", "Anaya",
         "Dev", "Kavya", "Yash", "Riya", "Neel", "Tara", "Aditya", "Zoya", "Krish", "Pihu",
         "Omar", "Nisha", "Rahul", "Ira", "Samar", "Leela", "Kunal", "Myra", "Farhan", "Avni"]
LAST = ["Sharma", "Iyer", "Khan", "Nair", "Gupta", "Reddy", "Das", "Mehta", "Joshi", "Bose",
        "Kapoor", "Menon", "Verma", "Pillai", "Singh", "Rao", "Malik", "Sen", "Patel", "Kulkarni"]


def font(name, size):
    return ImageFont.truetype(str(BUNDLED_FONTS[name]), size)


def date_text(day: date) -> str:
    return f"{day.day} {day:%B %Y}"


class Command(BaseCommand):
    help = "Insert illustrative sample data with demo logins (dev only). --reset replaces the seed's own data."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="Delete the seed's templates and certificates first.")
        parser.add_argument("--if-empty", action="store_true", help="Do nothing (successfully) when already seeded.")

    def handle(self, *args, reset=False, if_empty=False, **options):
        if not settings.DEBUG:
            raise CommandError("seed_fixtures only runs with DEBUG on.")
        issuer = User.objects.filter(username=ISSUER).first()
        if issuer and CertificateTemplate._base_manager.filter(owner=issuer).exists() and not reset:
            if if_empty:
                self.stdout.write("Sample data already exists; seeding skipped.")
                return
            raise CommandError("Sample data already exists. Use --reset to replace it.")

        self.rng = random.Random(2026)  # same data on every run
        self.password = os.environ.get("DEMO_PASSWORD") or secrets.token_urlsafe(9)
        self.created = []
        with transaction.atomic():
            if reset and issuer:
                self.clear(issuer)
            issuer = self.account(ISSUER, "Demo Issuer", role=RoleEnum.ISSUER)
            students = self.students()
            issued = sum(self.seed_template(issuer, spec, students) for spec in TEMPLATES)

        self.stdout.write(self.style.SUCCESS(
            f"Seeded {len(TEMPLATES)} templates and {issued} certificates for {len(students)} students."
        ))
        if self.created:
            shown = "the DEMO_PASSWORD from .env" if os.environ.get("DEMO_PASSWORD") else self.password
            self.stdout.write(self.style.SUCCESS(
                f"Logins: {ISSUER} and student01 to student{STUDENTS:02d}, password {shown} (dev only)."
            ))
        else:
            self.stdout.write(f"Logins {ISSUER} and student01 to student{STUDENTS:02d} exist (passwords unchanged).")

    # Accounts

    def account(self, username, name, **fields):
        "A demo login: created once; an existing one's password is never changed."
        user = User.objects.filter(username=username).first()
        if user:
            return user
        self.created.append(username)
        return User.objects.create_user(
            username, f"{username}@example.com", self.password, name=name, email_verified=True, **fields
        )

    def students(self):
        names = self.rng.sample([f"{first} {last}" for first in FIRST for last in LAST], STUDENTS)
        return [
            self.account(
                f"student{n:02d}",
                name,
                role=RoleEnum.RECIPIENT,
                roll_number=f"DEMO26-{n:03d}",
                date_of_birth=date(2004, 1, 1) + timedelta(days=self.rng.randint(0, 3 * 365)),
                institution=INSTITUTION,
            )
            for n, name in enumerate(names, start=1)
        ]

    # Templates and certificates

    def seed_template(self, issuer, spec, students) -> int:
        template = CertificateTemplate.objects.create(name=spec["name"], file=self.draw(spec), owner=issuer)
        boxes = [
            ("name", 300, 520, 1400, 140, "serif-regular", 96, TextAlignChoices.CENTER, BoxTypeChoices.TEXT),
            ("date", 200, 1140, 480, 60, "sans-regular", 36, TextAlignChoices.CENTER, BoxTypeChoices.TEXT),
            ("verify", 1600, 1000, 240, 240, "sans-regular", 32, TextAlignChoices.CENTER, BoxTypeChoices.VERIFY_QR),
            ("code", 1470, 1255, 500, 50, "sans-regular", 26, TextAlignChoices.CENTER, BoxTypeChoices.VERIFY_CODE),
        ]
        if spec["extra"]:
            boxes.append((spec["extra"][0], 400, 860, 1200, 80, "sans-bold", 48, TextAlignChoices.CENTER, BoxTypeChoices.TEXT))
        for key, x, y, width, height, face, size, alignment, kind in boxes:
            TemplateBox.objects.create(
                template=template, key=key, x=x, y=y, width=width, height=height,
                font=face, font_size=size, font_color=INK, alignment=alignment, type=kind,
            )

        issued_at = timezone.localtime().replace(hour=11, minute=0, second=0, microsecond=0) - timedelta(
            days=spec["days_ago"]
        )
        service = IssuanceService.for_manual(template)
        service.storage_provider = StorageProvider.LOCAL
        recipients = self.rng.sample(students, spec["recipients"])
        for i, student in enumerate(recipients):
            values = {"name": student.name, "date": date_text(issued_at.date())}
            if spec["extra"]:
                key, options = spec["extra"]
                # Hackathon teams of three; programmes in turn.
                values[key] = options[(i // 3 if key == "team" else i) % len(options)]
            service.issue_one(student.username, values)
        service.batch.certificates.update(issued_at=issued_at)

        if spec["recipients"] == STUDENTS:  # the workshop: revoke two, so recipients see that state too
            for certificate in self.rng.sample(list(service.batch.certificates.all()), 2):
                service.revoke_one(certificate)
                GeneratedCertificate.objects.filter(pk=certificate.pk).update(revoked_at=issued_at + timedelta(days=9))
        return len(recipients)

    def draw(self, spec) -> UploadFile:
        "The template artwork: borders, headings and static text, with room left for the boxes."
        image = Image.new("RGB", SIZE, PAPER)
        draw = ImageDraw.Draw(image)
        width, height = SIZE
        accent = spec["accent"]
        draw.rectangle((40, 40, width - 40, height - 40), outline=accent, width=14)
        draw.rectangle((72, 72, width - 72, height - 72), outline=accent, width=3)

        centre = width // 2
        draw.text((centre, 150), INSTITUTION.upper(), font=font("sans-bold", 34), fill=MUTED, anchor="mm")
        draw.text((centre, 270), "CERTIFICATE", font=font("serif-regular", 120), fill=accent, anchor="mm")
        draw.text((centre, 380), spec["kind"], font=font("sans-bold", 44), fill=INK, anchor="mm")
        draw.text((centre, 470), "This is to certify that", font=font("serif-regular", 40), fill=MUTED, anchor="mm")
        draw.line((500, 680, 1500, 680), fill=accent, width=3)
        for i, line in enumerate(spec["body"]):
            draw.text((centre, 740 + i * 60), line, font=font("sans-regular", 40), fill=INK, anchor="mm")

        # Signature lines: the date box sits above the left one, the right one is for a signature.
        for x0, label in ((200, "Date of issue"), (760, "Programme Coordinator")):
            draw.line((x0, 1210, x0 + 480, 1210), fill=MUTED, width=2)
            draw.text((x0 + 240, 1240), label, font=font("sans-regular", 28), fill=MUTED, anchor="mm")

        buffer = BytesIO()
        image.save(buffer, format="PNG")
        file = ContentFile(buffer.getvalue(), name=f"{spec['name'].lower().replace(' ', '-')}.png")
        file.content_type = "image/png"
        return UploadFile.upload(file, provider=StorageProvider.LOCAL)

    # Reset

    def clear(self, issuer):
        """Hard-delete the issuer's templates and everything hanging off them, unlike the app's soft delete:
        these rows are the seed's own, and a soft-deleted copy would only pile up on every reset."""
        templates = CertificateTemplate._base_manager.filter(owner=issuer)
        batches = CertificateBatch._base_manager.filter(template__in=templates)
        certificates = GeneratedCertificate._base_manager.filter(batch__in=batches)
        image_boxes = TemplateImageBox._base_manager.filter(template__in=templates)
        file_ids = {
            *templates.values_list("file", flat=True),
            *certificates.values_list("file", flat=True),
            *batches.values_list("file", flat=True),
            *batches.values_list("export_file", flat=True),
            *image_boxes.values_list("image", flat=True),
        } - {None}
        files = UploadFile._base_manager.filter(id__in=file_ids)
        local_keys = list(files.filter(provider=StorageProvider.LOCAL).values_list("key", flat=True))

        certificates.delete()
        batches.delete()
        TemplateBox._base_manager.filter(template__in=templates).delete()
        image_boxes.delete()
        templates.delete()
        files.delete()
        transaction.on_commit(lambda: [default_storage.delete(key) for key in local_keys])
