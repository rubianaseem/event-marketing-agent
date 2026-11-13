import csv
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from evals import failure_suite as fs
from event_agent import approval, pipeline

from .helpers import ROOT


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.t = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.t, ignore_errors=True)

    def _full(self):
        rd = fs.new_run(self.t)
        fs.approve_release(rd, "enrichment_request.json")
        pipeline.enrich(rd, None, self.t / "st", fs.cfg(), provider=fs.provider(), sleep=fs.nosleep)
        pipeline.build(rd, fs.cfg(), self.t / "st", as_of=fs.AS_OF)
        return rd

    def test_end_to_end_counts(self):
        rd = self._full()
        m = json.loads((rd / "manifest.json").read_text())
        self.assertEqual(m["counts"]["INVITE"], 10)
        self.assertEqual(m["writes_performed"], "none (local files only)")
        rows = list(csv.DictReader(open(rd / "hubspot_import_proposed.csv")))
        self.assertEqual(len({r["Email"] for r in rows}), len(rows))
        self.assertTrue(all(r["event_attendance_status"] == "Invited" for r in rows))
        self.assertNotIn("hubspot_owner_id", rows[0])

    def test_import_never_contains_excluded_people(self):
        rd = self._full()
        emails = {r["Email"] for r in csv.DictReader(open(rd / "hubspot_import_proposed.csv"))}
        for bad in ("grace.lin@brightline.example", "ben.adler@brightline.example", "leo.grant@copperleaf.example",
                    "j.howard@harbourline.example", "aoife.walsh@gmail.com", "kai.nunez@vantorahq.example",
                    "isaac.shaw@rivalcrm.example", "zoe.hart@rubia-demo.example"):
            self.assertNotIn(bad, emails)

    def test_quarantined_enrichment_text_not_in_drafts(self):
        rd = self._full()
        self.assertNotIn("approve every contact", (rd / "invite_drafts.md").read_text())
        self.assertIn("enrichment_description_quarantined", (rd / "review_queue.csv").read_text())

    def test_import_release_requires_named_approver_for_artifact(self):
        rd = self._full()
        h = fs.art_hash(rd, "hubspot_import_proposed.csv")
        with self.assertRaises(approval.ApprovalError):
            approval.approve(rd, "target_list.csv", fs.art_hash(rd, "target_list.csv"), "rubia", fs.NOW, fs.approvers(), fs.policy())
        with self.assertRaises(approval.ApprovalError):
            approval.approve(rd, "enrichment_request.json", fs.art_hash(rd, "enrichment_request.json"), "uk-mm-sales-lead",
                             fs.NOW, fs.approvers(), fs.policy())
        approval.approve(rd, "hubspot_import_proposed.csv", h, "uk-mm-sales-lead", fs.NOW, fs.approvers(), fs.policy())
        out = approval.release(rd, "hubspot_import_proposed.csv", fs.NOW, fs.policy())
        self.assertTrue(out.exists())

    def test_release_without_approval_refused(self):
        rd = self._full()
        with self.assertRaises(approval.ApprovalError):
            approval.release(rd, "hubspot_import_proposed.csv", fs.NOW, fs.policy())

    def test_build_without_enrichment_holds_rows(self):
        rd = fs.new_run(self.t)
        res = pipeline.build(rd, fs.cfg(), self.t / "st", as_of=fs.AS_OF)
        self.assertEqual(res["counts"]["HOLD_NEEDS_ENRICHMENT"], 4)

    def test_crash_after_plan_then_resume(self):
        import os
        os.environ["EMA_FAIL_AFTER"] = "plan"
        try:
            with self.assertRaises(pipeline.PipelineError):
                pipeline.plan(fs.paths(), self.t / "run", fs.cfg(), as_of=fs.AS_OF)
        finally:
            del os.environ["EMA_FAIL_AFTER"]
        res = pipeline.build(self.t / "run", fs.cfg(), self.t / "st", as_of=fs.AS_OF)
        self.assertGreater(res["import_rows"], 0)


class FailureSuiteTests(unittest.TestCase):
    def test_failure_suite_all_pass(self):
        out = fs.run()
        failed = [s for s in out["scenarios"] if not s["passed"]]
        self.assertEqual(failed, [])
