import datetime as dt
import unittest
import urllib.error
from io import BytesIO
from unittest.mock import patch

import digest
import outlook
import build_profile


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
        self.assertIn("could not be verified", errors[0])

    def test_greenhouse_specific_hosted_url(self):
        payload = {"jobs": [{"id": 123, "title": "Financial Analyst", "absolute_url": "https://example.com/search?gh_jid=123", "location": {"name": "Montreal"}, "first_published": "2026-11-02T10:00:00Z"}]}
        with patch.object(digest, "fetch", return_value=payload):
            jobs = list(digest.jobs_for({"type": "greenhouse", "board": "example"}))
        self.assertEqual(jobs[0]["url"], "https://job-boards.greenhouse.io/example/jobs/123")

    def test_company_url_fallback(self):
        class Response(BytesIO):
            status = 200
            url = "https://company.example/careers/jobs/123"
        job = dict(self.job, company_url="https://company.example/careers/jobs/123", job_id="123")
        with patch.object(digest.urllib.request, "urlopen", side_effect=[urllib.error.URLError("blocked"), Response(b"Financial Analyst - Apply")]):
            self.assertTrue(digest.listing_available(job))
        self.assertEqual(job["url"], "https://company.example/careers/jobs/123")

    def test_search_url_is_not_used_as_fallback(self):
        job = dict(self.job, company_url="https://company.example/jobs/search?gh_jid=123", job_id="123")
        with patch.object(digest.urllib.request, "urlopen", side_effect=urllib.error.URLError("blocked")) as request:
            self.assertFalse(digest.listing_available(job))
        self.assertEqual(request.call_count, 1)

    def test_refresh_failure_is_reported(self):
        config = {"microsoft_client_id": "test", "keychain_service": "test"}
        with patch.object(outlook.subprocess, "check_output", return_value="token"), patch.object(outlook, "form", return_value={"error_description": "expired"}):
            with self.assertRaisesRegex(RuntimeError, "expired"):
                outlook.access_token(config)

    def test_resume_profile_is_reviewable_and_local(self):
        profile = build_profile.suggest("Financial analysis with Excel and PowerBI", {"minimum_skill_matches": 2})
        self.assertIn("excel", profile["skills"])
        self.assertIn("power bi", profile["skills"])
        self.assertIn("financial", profile["domain_terms"])
        self.assertEqual(profile["preferred_locations"], [])

    def test_profile_changes_matching(self):
        profile = {"domain_terms": ["software"], "skills": ["python", "sql"], "excluded_title_terms": [], "preferred_locations": [], "minimum_skill_matches": 2}
        self.assertIsNone(digest.evaluate(self.job.copy(), self.now, profile))
        job = dict(self.job, title="Developer", description="software Python SQL")
        self.assertIsNotNone(digest.evaluate(job, self.now, profile))


if __name__ == "__main__":
    unittest.main()
