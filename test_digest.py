import datetime as dt
import unittest
from unittest.mock import patch

import digest
import outlook


class DigestTests(unittest.TestCase):
    def setUp(self):
        self.now = dt.datetime(2026, 11, 2, 12, tzinfo=dt.timezone.utc)
        self.job = {"title": "Financial Analyst", "description": "financial analysis, accounting, SAP and Excel",
                    "location": "Montréal, Canada", "posted": self.now - dt.timedelta(hours=2),
                    "url": "https://boards.greenhouse.io/company/jobs/123", "source": "Greenhouse", "company": "company"}

    def test_relevant(self):
        self.assertIsNotNone(digest.evaluate(self.job.copy(), self.now))

    def test_old_and_missing_date(self):
        for posted in (None, self.now - dt.timedelta(hours=49)):
            self.assertIsNone(digest.evaluate(dict(self.job, posted=posted), self.now))

    def test_no_sponsorship_and_sales(self):
        self.assertIsNone(digest.evaluate(dict(self.job, location="New York, NY", description="financial analysis SAP Excel; sponsorship not provided"), self.now))
        self.assertIsNone(digest.evaluate(dict(self.job, title="Account Executive Sales"), self.now))

    def test_generic_search_page_rejected(self):
        self.assertIsNone(digest.evaluate(dict(self.job, url="https://example.com/jobs/search"), self.now))

    def test_inactive_listing_excluded(self):
        with patch.object(digest, "jobs_for", return_value=[self.job]), patch.object(digest, "listing_available", return_value=False):
            results, errors = digest.collect({"sources": [{"type": "lever", "board": "example"}]}, self.now)
        self.assertEqual(results, [])
        self.assertEqual(errors, [])

    def test_refresh_failure_is_reported(self):
        config = {"microsoft_client_id": "test", "keychain_service": "test"}
        with patch.object(outlook.subprocess, "check_output", return_value="token"), patch.object(outlook, "form", return_value={"error_description": "expired"}):
            with self.assertRaisesRegex(RuntimeError, "expired"):
                outlook.access_token(config)


if __name__ == "__main__":
    unittest.main()
