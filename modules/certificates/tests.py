from datetime import date
from unittest import mock

from django.test import TestCase
from rest_framework.test import APIClient

from modules.certificates.models import CertificateBatch, CertificateTemplate, GeneratedCertificate, TemplateBox
from modules.certificates.services import IssuanceError, IssuanceService
from modules.core.models import UploadFile, User


def upload(name="file.png"):
    return UploadFile.objects.create(key=name, url=f"https://files.example.com/{name}", original_filename=name)


def fake_render(batch, index, row, boxes, image_boxes):
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
