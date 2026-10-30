import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from database import Database


class VerifiedReporterDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = Database(
            Path(self.temp_dir.name) / "verification.sqlite",
            backend="sqlite",
        )

    async def asyncTearDown(self):
        self.db._conn.close()
        self.temp_dir.cleanup()

    async def _submit(self, user_id: int) -> int:
        now = datetime.now(timezone.utc)
        ticket = f"ticket-{user_id}"
        request_id = await self.db.create_verification_ticket(
            user_id,
            ticket,
            b"encrypted-phone",
            (now + timedelta(minutes=10)).isoformat(timespec="seconds"),
            now.isoformat(timespec="seconds"),
            "reporter-consent-v1",
        )
        self.assertIsNotNone(request_id)
        await self.db.submit_verification_documents(
            user_id,
            ticket,
            {
                "selfie": b"encrypted-selfie",
                "id_front": b"encrypted-front",
                "id_back": b"encrypted-back",
            },
        )
        return request_id

    async def test_reporter_status_and_decision_transition(self):
        await self.db.record_user(77, "reporter", "مراسل")
        request_id = await self._submit(77)

        pending = await self.db.list_verification_requests()
        self.assertEqual(pending[0]["id"], request_id)
        self.assertEqual(pending[0]["status"], "pending")
        self.assertEqual(pending[0]["has_documents"], 1)

        users, _ = await self.db.list_users()
        self.assertEqual(users[0]["verification_status"], "pending")
        self.assertTrue(
            await self.db.decide_verification(request_id, 9001, "verified")
        )
        self.assertFalse(
            await self.db.decide_verification(
                request_id, 9002, "rejected", "طلب مكرر بعد القرار"
            )
        )

        users, _ = await self.db.list_users()
        self.assertEqual(users[0]["verification_status"], "verified")
        self.assertEqual(
            await self.db.verification_document(request_id, "selfie"),
            b"encrypted-selfie",
        )

    async def test_pending_documents_are_kept_and_decided_documents_expire(self):
        pending_request = await self._submit(78)
        decided_request = await self._submit(79)
        self.assertTrue(
            await self.db.decide_verification(
                decided_request, 9001, "rejected", "الوثيقة غير واضحة"
            )
        )
        approved_request = await self._submit(81)
        self.assertTrue(
            await self.db.decide_verification(
                approved_request, 9001, "verified"
            )
        )

        old_decision = (
            datetime.now(timezone.utc) - timedelta(days=31)
        ).isoformat(timespec="seconds")
        self.db._conn.execute(
            "UPDATE verified_reporters SET decided_at = ? "
            "WHERE id IN (?, ?)",
            (old_decision, decided_request, approved_request),
        )
        self.db._conn.commit()

        deleted = self.db._cleanup_private_data()

        self.assertEqual(deleted["verification_documents_deleted"], 1)
        self.assertIsNotNone(
            await self.db.verification_document(pending_request, "selfie")
        )
        self.assertIsNone(
            await self.db.verification_document(decided_request, "selfie")
        )
        self.assertIsNotNone(
            await self.db.verification_document(approved_request, "selfie")
        )
        self.assertIsNone(await self.db.verification_status(79))
        self.assertIsNone(
            await self.db.verification_request(decided_request)
        )
        self.assertIsNotNone(
            await self.db.verification_request(approved_request)
        )

    async def test_rejection_requires_a_reason(self):
        request_id = await self._submit(82)
        with self.assertRaisesRegex(
            ValueError, "verification_rejection_reason_required"
        ):
            await self.db.decide_verification(request_id, 9001, "rejected")

    async def test_approved_reporter_deletion_requires_approval_and_is_audited(self):
        request_id = await self._submit(83)
        self.assertIsNone(
            await self.db.delete_verified_reporter(request_id, 9001, "قبل الاعتماد")
        )
        self.assertTrue(
            await self.db.decide_verification(request_id, 9001, "verified")
        )
        deleted = await self.db.delete_verified_reporter(
            request_id, 9001, "طلب المراسل"
        )
        self.assertEqual(deleted["user_id"], 83)
        self.assertIsNone(
            await self.db.verification_document(request_id, "selfie")
        )
        audit = await self.db.recent_audit(limit=10)
        self.assertEqual(audit[0]["action"], "verified_reporter_documents_deleted")
        self.assertIn("طلب المراسل", audit[0]["details"])

    async def test_capture_ticket_can_be_extended_and_cancelled(self):
        user_id = 80
        ticket = "capture-ticket"
        now = datetime.now(timezone.utc)
        request_id = await self.db.create_verification_ticket(
            user_id,
            ticket,
            b"encrypted-phone",
            (now + timedelta(minutes=5)).isoformat(timespec="seconds"),
            now.isoformat(timespec="seconds"),
            "reporter-consent-v1",
        )
        self.assertIsNotNone(request_id)

        self.assertTrue(
            await self.db.extend_verification_ticket(
                user_id,
                ticket,
                (now + timedelta(minutes=30)).isoformat(timespec="seconds"),
            )
        )
        self.assertTrue(
            await self.db.cancel_verification_capture(user_id, ticket)
        )
        self.assertIsNone(await self.db.verification_status(user_id))
        self.assertFalse(
            await self.db.cancel_verification_capture(user_id, ticket)
        )
