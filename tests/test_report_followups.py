import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from database import (
    Database,
    STATUS_AWAITING_INFO,
    STATUS_CLOSED_NO_RESPONSE,
    STATUS_REJECTED,
    STATUS_REVIEW,
)


class ReportFollowupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = Database(
            Path(self.temp_dir.name) / "reports.sqlite",
            backend="sqlite",
        )
        self.report_id = self.db._conn.execute(
            """
            INSERT INTO reports
                (seq, user_id, photo_path, post_link, description, status, created_at)
            VALUES ('MRV-TEST-1', 42, 'unused', '', 'test report', ?, ?)
            """,
            (
                STATUS_REVIEW,
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
            ),
        ).lastrowid
        self.db._conn.commit()

    async def asyncTearDown(self):
        self.db._conn.close()
        self.temp_dir.cleanup()

    async def test_followup_deadline_closes_without_reopening(self):
        due_at = (
            datetime.now(timezone.utc) + timedelta(days=2)
        ).isoformat(timespec="seconds")
        self.assertTrue(
            await self.db.set_followup_request(
                self.report_id, "يرجى توضيح التاريخ", due_at
            )
        )
        waiting = await self.db.get_by_id(self.report_id)
        self.assertEqual(waiting["status"], STATUS_AWAITING_INFO)
        self.assertEqual(waiting["followup_due_at"], due_at)

        closed = await self.db.close_expired_followups(
            (datetime.fromisoformat(due_at) + timedelta(seconds=1)).isoformat(
                timespec="seconds"
            )
        )
        self.assertEqual([item["id"] for item in closed], [self.report_id])
        report = await self.db.get_by_id(self.report_id)
        self.assertEqual(report["status"], STATUS_CLOSED_NO_RESPONSE)
        self.assertIsNone(report["followup_due_at"])

    async def test_response_returns_report_to_review(self):
        due_at = (
            datetime.now(timezone.utc) + timedelta(days=2)
        ).isoformat(timespec="seconds")
        await self.db.set_followup_request(self.report_id, "تفصيل إضافي", due_at)
        self.assertTrue(
            await self.db.save_followup_response(self.report_id, "هذا هو التفصيل")
        )
        report = await self.db.get_by_id(self.report_id)
        self.assertEqual(report["status"], STATUS_REVIEW)
        self.assertEqual(report["followup_response"], "هذا هو التفصيل")
        self.assertEqual(
            await self.db.close_expired_followups(
                (datetime.fromisoformat(due_at) + timedelta(seconds=1)).isoformat(
                    timespec="seconds"
                )
            ),
            [],
        )

    async def test_rejected_report_appeal_is_recorded_without_reopening(self):
        self.db._conn.execute(
            "UPDATE reports SET status = ? WHERE id = ?",
            (STATUS_REJECTED, self.report_id),
        )
        self.db._conn.commit()
        appeal_id = await self.db.add_report_appeal(
            self.report_id, 42, "أرفق معلومة إضافية"
        )
        self.assertIsNotNone(appeal_id)
        report = await self.db.get_by_id(self.report_id)
        self.assertEqual(report["status"], STATUS_REJECTED)
        self.assertEqual(report["needs_review"], 1)
        appeals = await self.db.list_report_appeals(self.report_id)
        self.assertEqual(appeals[0]["id"], appeal_id)
        self.assertEqual(appeals[0]["message"], "أرفق معلومة إضافية")


if __name__ == "__main__":
    unittest.main()
