import tempfile
from datetime import date
from io import StringIO
from unittest import mock

from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings
from PIL import Image
from rest_framework.test import APIClient

from modules.certificates.api.v1.views import scale_boxes
from modules.certificates.models import CertificateBatch, CertificateTemplate, GeneratedCertificate, TemplateBox
from modules.certificates.rendering import box_values, draw_qr, generate_certificate_for_row
from modules.certificates.services import IssuanceError, IssuanceService
from modules.certificates.verification import format_code, new_code, normalise_code
from modules.core.models import UploadFile, User


def upload(name="file.png"):
    return UploadFile.objects.create(key=name, url=f"https://files.example.com/{name}", original_filename=name)


def fake_render(batch, index, row, boxes, image_boxes, code, provider=None):
    return upload(f"certificate_{index}.png"), []


@mock.patch("modules.certificates.services.render_file", fake_render)
class IssuanceTests(TestCase):
    def setUp(self):
        self.issuer = User.objects.create_user("issuer", "issuer@example.com", "x", role="issuer")
        recipient = lambda username, roll, verified=True, **kw: User.objects.create_user(  # noqa: E731
            username, f"{username}@example.com", "x", role="recipient", email_verified=verified, roll_number=roll, **kw
        )
        self.asha = recipient("asha", "R1", phone="9876543210", date_of_birth=date(2004, 12, 31))
        recipient("bina", "R3", verified=False)
        recipient("dup1", "DUP")
        recipient("dup2", "dup")

        template = CertificateTemplate.objects.create(name="Workshop", file=upload("template.png"), owner=self.issuer)
        TemplateBox.objects.create(template=template, key="name", x=0, y=0, width=10, height=10)
        self.batch = CertificateBatch.objects.create(
            template=template,
            owner=self.issuer,
            file=upload("rows.csv"),
            csv_columns=["Name", "Roll", "Phone", "DOB"],
            mapping={"name": "Name"},
        )
        rows = [
            ("Asha", "r1", "+91 98765-43210", "31/12/2004"),
            ("Bina", "R3", "", ""),  # unverified account
            ("Dup", "DUP", "", ""),  # two accounts claim it
            ("Nobody", "R9", "", ""),
        ]
        self.certs = [
            GeneratedCertificate.objects.create(
                batch=self.batch,
                row_index=i,
                row_data=dict(zip(self.batch.csv_columns, row)),
                file=upload(),
                is_successful=True,
            )
            for i, row in enumerate(rows)
        ]
        self.failed = GeneratedCertificate.objects.create(
            batch=self.batch, row_index=9, row_data={"Name": "Broken", "Roll": "R1"}, is_successful=False
        )
        self.service = IssuanceService(self.batch)

    def client_for(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    def test_match_assigns_only_single_verified_hits(self):
        counts = self.service.match({"roll_number": "Roll"})
        self.assertEqual(counts, {"unassigned": 3, "ready": 1, "issued": 0, "revoked": 0, "failed": 1})
        asha, bina, dup, nobody = (GeneratedCertificate.objects.get(pk=c.pk) for c in self.certs)
        self.assertEqual(asha.recipient, self.asha)
        self.assertEqual(bina.match_error, "No verified recipient matches")
        self.assertEqual(dup.match_error, "More than one recipient matches")
        self.assertIsNone(GeneratedCertificate.objects.get(pk=self.failed.pk).recipient)

    def test_match_needs_every_field_to_agree_and_normalises_phone_and_date(self):
        self.service.match({"roll_number": "Roll", "phone": "Phone", "date_of_birth": "DOB"})
        self.assertEqual(GeneratedCertificate.objects.get(pk=self.certs[0].pk).recipient, self.asha)
        self.service.match({"roll_number": "Roll", "phone": "Name"}, overwrite=True)
        self.assertIsNone(GeneratedCertificate.objects.get(pk=self.certs[0].pk).recipient)

    def test_rematch_keeps_manual_assignment_unless_overwrite(self):
        self.service.assign(self.certs[3], "ASHA@example.com")
        self.service.match({"roll_number": "Roll"})
        self.assertEqual(GeneratedCertificate.objects.get(pk=self.certs[3].pk).recipient, self.asha)
        self.service.match({"roll_number": "Roll"}, overwrite=True)
        self.assertIsNone(GeneratedCertificate.objects.get(pk=self.certs[3].pk).recipient)

    def test_unverified_or_unknown_recipient_cannot_be_assigned(self):
        for identifier in ("bina", "nobody@example.com"):
            with self.assertRaises(IssuanceError):
                self.service.assign(self.certs[3], identifier)

    def test_issue_then_recipient_sees_it_and_revoke_hides_the_file(self):
        self.service.match({"roll_number": "Roll"})
        counts = self.service.issue()
        self.assertEqual((counts["issued"], counts["unassigned"], counts["failed"]), (1, 3, 1))

        received = self.client_for(self.asha).get("/certificates/").json()["results"]
        self.assertEqual(len(received), 1)
        self.assertEqual((received[0]["template_name"], received[0]["issue_status"]), ("Workshop", "issued"))
        self.assertIsNotNone(received[0]["file"])

        cert = GeneratedCertificate.objects.get(pk=self.certs[0].pk)
        with self.assertRaises(IssuanceError):  # an issued certificate never moves to someone else
            self.service.assign(cert, None)
        self.service.revoke_one(cert)
        received = self.client_for(self.asha).get("/certificates/").json()["results"]
        self.assertEqual((received[0]["issue_status"], received[0]["file"]), ("revoked", None))

    def test_edit_values_rerenders_and_issue_one_issues(self):
        self.service.edit_values(self.certs[1], {"name": "Bina K"})
        self.assertEqual(GeneratedCertificate.objects.get(pk=self.certs[1].pk).row_data["Name"], "Bina K")

        manual = IssuanceService.for_manual(self.batch.template)
        first = manual.issue_one("asha", {"name": "Asha"})
        second = manual.issue_one("asha", {"name": "Asha again"})
        self.assertEqual((first.issue_status, first.row_index, second.row_index), ("issued", 0, 1))
        self.assertEqual(IssuanceService.for_manual(self.batch.template).batch, manual.batch)

    def test_template_with_issued_certificates_cannot_be_deleted(self):
        self.service.match({"roll_number": "Roll"})
        self.service.issue()
        client = self.client_for(self.issuer)
        self.assertEqual(client.delete(f"/certificate-template/{self.batch.template.uuid}/").status_code, 409)
        self.service.revoke()
        self.assertEqual(client.delete(f"/certificate-template/{self.batch.template.uuid}/").status_code, 200)
        # The revoked certificate's template is gone; the recipient's list must still load.
        self.assertEqual(self.client_for(self.asha).get("/certificates/").status_code, 200)

    def test_api_issue_one(self):
        r = self.client_for(self.issuer).post(
            f"/certificate-template/{self.batch.template.uuid}/issue-one/",
            {"recipient": "asha", "values": {"name": "Asha"}},
            format="json",
        )
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual((r.json()["issue_status"], r.json()["recipient"]["username"]), ("issued", "asha"))

    def test_roles_are_enforced_with_403(self):
        self.assertEqual(self.client_for(self.asha).get("/certificate-template/").status_code, 403)
        self.assertEqual(self.client_for(self.issuer).get("/certificates/").status_code, 403)

    def test_api_match_and_filter(self):
        client = self.client_for(self.issuer)
        r = client.post(f"/certificate-batches/{self.batch.uuid}/match/", {"match_fields": {"roll_number": "Roll"}}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        ready = client.get(f"/certificate-batches/{self.batch.uuid}/certificates/?filter=ready").json()["results"]
        self.assertEqual([c["recipient"]["username"] for c in ready], ["asha"])
        self.assertNotIn("phone", ready[0]["recipient"])
        bad = client.post(f"/certificate-batches/{self.batch.uuid}/match/", {"match_fields": {"roll_number": "Nope"}}, format="json")
        self.assertEqual(bad.status_code, 409)

    def test_public_verify_shows_only_issued_details(self):
        self.asha.name = "Asha Sample"
        self.asha.save()
        cert = self.certs[0]
        cert.recipient, cert.verification_code = self.asha, "7KQ2M9XD4TFA"
        cert.save()
        url = "/certificates/verify/7kq2-m9xd-4tfa/"

        self.assertEqual(APIClient().get(url).status_code, 409)  # still a draft
        self.service.issue()
        response = APIClient().get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            {k: response.json()[k] for k in ("code", "issue_status", "recipient_name", "template_name")},
            {"code": "7KQ2-M9XD-4TFA", "issue_status": "issued", "recipient_name": "Asha Sample", "template_name": "Workshop"},
        )
        self.assertNotIn("row_data", response.json())

        self.service.revoke()
        self.assertEqual(APIClient().get(url).json()["issue_status"], "revoked")
        self.assertEqual(APIClient().get("/certificates/verify/nonsense/").status_code, 409)

    def test_mapping_rejects_verify_boxes(self):
        TemplateBox.objects.create(template=self.batch.template, key="code", type="verify_code", x=0, y=0, width=10, height=10)
        response = self.client_for(self.issuer).put(
            f"/certificate-batches/{self.batch.uuid}/mapping/", {"mapping": {"code": "Name"}}, format="json"
        )
        self.assertEqual(response.status_code, 409)

    def test_generation_and_issue_one_save_a_code(self):
        with mock.patch("modules.certificates.rendering.render_file", fake_render):
            cert = generate_certificate_for_row(self.batch, 20, {"Name": "New"}, [], [])
        self.assertEqual(len(cert.verification_code), 12)
        TemplateBox.objects.create(template=self.batch.template, key="code", type="verify_code", x=0, y=0, width=10, height=10)
        manual = IssuanceService.for_manual(self.batch.template).issue_one("asha", {"name": "Asha"})
        self.assertEqual(len(manual.verification_code), 12)
        self.assertEqual(manual.row_data, {"name": "Asha"})


class VerificationCodeTests(TestCase):
    def test_codes_survive_formatting_and_common_typos(self):
        code = new_code()
        self.assertEqual(len(code), 12)
        self.assertEqual(normalise_code(format_code(code)), code)
        self.assertEqual(normalise_code(" 7kq2-m9xd 4tfa "), "7KQ2M9XD4TFA")
        self.assertEqual(normalise_code("OIL0-0000-0000"), "0110" + "0" * 8)
        self.assertIsNone(normalise_code("7KQ2-M9XD"))
        self.assertIsNone(normalise_code("7KQ2-M9XD-4TF!"))

    @override_settings(FRONTEND_URL="https://app.example.com")
    def test_box_values_fill_verify_boxes_from_the_code(self):
        batch = CertificateBatch(mapping={"name": "Name", "link": "Link"})
        boxes = [
            TemplateBox(key="name"),
            TemplateBox(key="link", type="qr"),
            TemplateBox(key="code", type="verify_code"),
            TemplateBox(key="scan", type="verify_qr"),
        ]
        values = box_values(batch, {"Name": "Asha", "Link": "https://example.com/a"}, boxes, "7KQ2M9XD4TFA")
        self.assertEqual(
            values,
            {
                "name": "Asha",
                "link": "https://example.com/a",
                "code": "7KQ2-M9XD-4TFA",
                "scan": "https://app.example.com/verify?code=7KQ2-M9XD-4TFA",
            },
        )

    def test_draw_qr_paints_inside_its_box_only(self):
        image = Image.new("RGB", (200, 200), "white")
        box = TemplateBox(key="scan", type="qr", x=50, y=50, width=100, height=100, font_color="#ff0000")
        self.assertIsNone(draw_qr(image, box, "https://example.com"))
        inside = image.crop((50, 50, 150, 150)).getcolors()
        self.assertIn((255, 0, 0), [colour for _, colour in inside])
        self.assertEqual(image.crop((0, 0, 50, 200)).getcolors(), [(50 * 200, (255, 255, 255))])
        self.assertIsNotNone(draw_qr(image, TemplateBox(key="tiny", x=0, y=0, width=5, height=5), "x" * 50))


class CanvaTemplateTests(TestCase):
    def setUp(self):
        self.issuer = User.objects.create_user("issuer", "issuer@example.com", "x", role="issuer")
        self.template = CertificateTemplate.objects.create(
            name="Workshop", file=upload("template.png"), owner=self.issuer, canva_design_id="D1", canva_page=1
        )

    # The whole service is replaced: building a real one needs Canva keys, which test runs don't have.
    @mock.patch("modules.certificates.api.v1.views.CanvaService")
    def test_reimporting_a_page_returns_the_existing_template(self, canva):
        client = APIClient()
        client.force_authenticate(self.issuer)
        response = client.post("/certificate-template/import-canva/", {"design_id": "D1", "name": "Again"}, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.json()["already_imported"])
        self.assertEqual(response.json()["template"]["uuid"], str(self.template.uuid))
        canva.assert_not_called()
        self.assertEqual(CertificateTemplate.objects.count(), 1)

    @mock.patch("modules.certificates.api.v1.views.store_canva_export", lambda image: upload("export.png"))
    @mock.patch("modules.certificates.api.v1.views.CanvaService")
    def test_import_that_loses_a_race_returns_the_winner(self, canva):
        export_design = canva.return_value.export_design
        export_design.return_value = mock.Mock(width=2480, height=1754, downscaled=False)
        client = APIClient()
        client.force_authenticate(self.issuer)
        # The first lookup misses (the other request hasn't committed yet), so the insert hits the constraint.
        real_filter = CertificateTemplate.objects.filter
        calls = []

        def first_lookup_misses(*args, **kwargs):
            calls.append(kwargs)
            return CertificateTemplate.objects.none() if len(calls) == 1 else real_filter(*args, **kwargs)

        with mock.patch.object(CertificateTemplate.objects, "filter", side_effect=first_lookup_misses):
            response = client.post("/certificate-template/import-canva/", {"design_id": "D1", "name": "Again"}, format="json")
        export_design.assert_called_once()  # got past the first lookup, so the insert really ran
        self.assertEqual(len(calls), 2)
        self.assertTrue(response.json()["already_imported"])
        self.assertEqual(response.json()["template"]["uuid"], str(self.template.uuid))
        self.assertEqual(CertificateTemplate.objects.count(), 1)

    def test_scale_boxes_keeps_qr_boxes_square(self):
        TemplateBox.objects.create(template=self.template, key="name", x=10, y=20, width=300, height=41, font_size=32)
        TemplateBox.objects.create(template=self.template, key="scan", type="verify_qr", x=5, y=5, width=99, height=99)
        scale_boxes(self.template, 2)
        name, scan = TemplateBox.objects.order_by("key")
        self.assertEqual((name.x, name.y, name.width, name.height, name.font_size), (20, 40, 600, 82, 64))
        self.assertEqual((scan.width, scan.height), (198, 198))


@mock.patch("modules.certificates.services.render_file", fake_render)
@override_settings(DEBUG=True, MEDIA_ROOT=tempfile.mkdtemp())
class SeedFixturesTests(TestCase):
    def counts(self):
        issued = GeneratedCertificate.objects.filter(batch__template__owner__username="demo_issuer")
        return {
            "students": User.objects.filter(role="recipient", username__startswith="student", email_verified=True).count(),
            "templates": CertificateTemplate.objects.filter(owner__username="demo_issuer").count(),
            "issued": issued.filter(issue_status="issued").count(),
            "revoked": issued.filter(issue_status="revoked").count(),
        }

    def test_seeds_once_and_reset_replaces_only_its_own_data(self):
        other = User.objects.create_user("someone", "someone@example.com", "x", role="issuer")
        CertificateTemplate.objects.create(name="Not seed data", file=upload(), owner=other)

        call_command("seed_fixtures", stdout=StringIO())
        expected = {"students": 50, "templates": 3, "issued": 88, "revoked": 2}
        self.assertEqual(self.counts(), expected)
        student = User.objects.get(username="student01")
        self.assertTrue(GeneratedCertificate.objects.filter(recipient=student, issue_status="issued").exists())

        with self.assertRaises(CommandError):
            call_command("seed_fixtures", stdout=StringIO())
        call_command("seed_fixtures", "--if-empty", stdout=StringIO())
        call_command("seed_fixtures", "--reset", stdout=StringIO())
        self.assertEqual(self.counts(), expected)
        self.assertTrue(CertificateTemplate.objects.filter(name="Not seed data").exists())
