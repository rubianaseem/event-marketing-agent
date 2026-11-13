import unittest

from event_agent.dedupe import classify_domain, dedupe_in_file, suggest_domain
from event_agent.ingest import load_candidates, norm_company, norm_country, parse_employees

from .helpers import POLICY, ROOT, crm, matched, row


class IngestTests(unittest.TestCase):
    def test_country_variants(self):
        self.assertEqual(norm_country("England"), "UK")
        self.assertEqual(norm_country("Republic of Ireland"), "IE")
        self.assertEqual(norm_country("USA"), "US")
        self.assertEqual(norm_country(""), "")

    def test_employee_formats(self):
        self.assertEqual(parse_employees("1.2k"), 1200)
        self.assertEqual(parse_employees("201-500"), 500)
        self.assertEqual(parse_employees("1,100"), 1100)
        self.assertIsNone(parse_employees("lots"))

    def test_company_suffixes(self):
        self.assertEqual(norm_company("Kestrel Cloud Ltd"), norm_company("Kestrel Cloud"))

    def test_sample_emails_normalised(self):
        rows = load_candidates(ROOT / "sample_candidates.csv")
        self.assertEqual(rows[0]["email"], "maya.patel@northwind-analytics.example")
        self.assertTrue(rows[0]["email_raw_changed"])


class DedupeTests(unittest.TestCase):
    def test_in_file_merge_keeps_restrictive_opt_in_and_fills_blanks(self):
        a = row(row=1, email="x@a.example", employees=None, marketing_opt_in=True, merged_rows=[1])
        b = row(row=2, email="x@a.example", employees=300, marketing_opt_in=False, source="past_event", merged_rows=[2])
        out, merges = dedupe_in_file([a, b])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["employees"], 300)
        self.assertIs(out[0]["marketing_opt_in"], False)
        self.assertEqual(out[0]["source"], "partner_list+past_event")
        self.assertEqual(merges, [{"kept_row": 1, "merged_row": 2}])

    def test_domain_classes(self):
        self.assertEqual(classify_domain("a@gmail.com", POLICY), "personal")
        self.assertEqual(classify_domain("a@rivalcrm.example", POLICY), "competitor")
        self.assertEqual(classify_domain("a@rubia-demo.example", POLICY), "internal")

    def test_personal_email_matches_company_by_name(self):
        r, _ = matched(email="someone@gmail.com", company="Kestrel Cloud Ltd")
        self.assertEqual((r["company_id"], r["company_match"]), ("c104", "company_name"))

    def test_possible_duplicate_by_name_and_company(self):
        r, _ = matched(email="ruth.osei@northwind-analytics.example", first_name="Ruth", last_name="Osei", company="Northwind")
        self.assertEqual(r["possible_duplicates"], ["107"])

    def test_backfill_from_crm_company(self):
        r, _ = matched(email="new@kestrelcloud.example", company="Kestrel Cloud", country="", employees=None, industry="")
        self.assertEqual((r["country"], r["employees"]), ("IE", 320))

    def test_invalid_email_domain_suggestion(self):
        self.assertEqual(suggest_domain("tom@brightline", crm()), "brightline.example")
