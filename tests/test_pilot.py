import json
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest.mock import patch

from tokenify_pilot import cli
from tokenify_pilot.pilot_lib import digest, read, messages


def run_command(entry, *args):
    output = StringIO()
    with patch("sys.argv", [entry, *args]), redirect_stdout(output), \
            patch("urllib.request.urlopen", side_effect=AssertionError("Unexpected network call")), \
            patch("tokenify_pilot.cli.api_call", side_effect=AssertionError("Unexpected model call")):
        if entry == "run.py":
            runpy.run_path(str(cli.ROOT / entry), run_name="__main__")
        else:
            runpy.run_module("tokenify_pilot", run_name="__main__")
    return json.loads(output.getvalue())

class PilotTests(unittest.TestCase):
    def test_historical_audit(self):
        self.assertEqual(cli.audit()["status_counts"],{"ok":74,"invalid_label":16})
        self.assertEqual(cli.audit()["invalid_reasons"],{
            "missing_required_fields":6,"leading_space_in_rationale_key":5,"invalid_json":5})

    def test_job_reversal_and_blinding(self):
        req=read(cli.PILOT_DATA/"pilot_requests.jsonl")
        res=read(cli.PILOT_DATA/"pilot_responses.jsonl")
        batch=list(cli.jobs(req,res))
        self.assertEqual(len(batch),90)
        self.assertEqual(len({j["job_id"] for j in batch}),90)
        self.assertEqual(digest(batch), "c59723a9308c3c38dcbafcceb4597963b0b00d0e6dce6b0e4071e52e7b11cc0d")
        for a,b in zip(batch[::2],batch[1::2]):
            self.assertEqual(a["payload"]["reference_senses"],b["payload"]["reference_senses"][::-1])
            self.assertEqual(a["payload"]["answer"],b["payload"]["answer"])
            self.assertNotIn("gpt-5.6-sol",json.dumps(a["payload"]))
            self.assertNotIn("reviewer",json.dumps(a["payload"]))

    def test_messages_match_archive(self):
        cues=json.loads((cli.PILOT_DATA/"cues_v1.json").read_text())
        for q in read(cli.PILOT_DATA/"pilot_requests.jsonl"):
            self.assertEqual(messages(q["question"],q["condition"],q["cue_field"],cues.get(q["cue_field"])),q["messages"])

    def test_invalid_is_not_zero(self):
        j=next(cli.jobs(read(cli.PILOT_DATA/"pilot_requests.jsonl"),read(cli.PILOT_DATA/"pilot_responses.jsonl")))
        self.assertEqual(cli.check("not json",j),("invalid_label",None))
        self.assertEqual(cli.check("{}",j),("invalid_label",None))

    def test_reference_ids_stable(self):
        qs=read(cli.PILOT_DATA/"pilot_requests.jsonl")
        self.assertEqual(sum(q["condition"]=="C0" for q in qs),9)
        for q in qs:
            self.assertEqual(len({r["sense_id"] for r in q["references"]}),2)
            self.assertTrue(all(r["url"].startswith("https://") for r in q["references"]))

    def test_root_wrapper_preserves_imports(self):
        import run
        self.assertIs(run.audit, cli.audit)
        self.assertIs(run.jobs, cli.jobs)
        self.assertEqual(run.ROOT, cli.ROOT)
        self.assertEqual(run.RUBRIC, cli.RUBRIC)

    def test_audit_entrypoints_are_offline_and_match(self):
        expected = cli.audit()
        self.assertEqual(expected["requests"], 45)
        self.assertEqual(expected["responses"], 45)
        self.assertEqual(expected["judge_calls"], 90)
        for entry in ("run.py", "tokenify_pilot"):
            with self.subTest(entry=entry):
                self.assertEqual(run_command(entry, "audit"), expected)

    def test_generate_defaults_to_offline_dry_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            for entry in ("run.py", "tokenify_pilot"):
                with self.subTest(entry=entry):
                    output = Path(tmp) / (entry + ".jsonl")
                    result = run_command(entry, "generate", "--model", "unused-model", "--out", str(output))
                    self.assertTrue(result["dry_run"])
                    self.assertEqual(result["requests"], 45)
                    self.assertEqual(result["limit"], 1)
                    self.assertFalse(output.exists())

    def test_judge_defaults_to_offline_dry_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            jobs_path = Path(tmp) / "jobs.jsonl"
            batch = list(cli.jobs(read(cli.PILOT_DATA/"pilot_requests.jsonl"), read(cli.PILOT_DATA/"pilot_responses.jsonl")))
            jobs_path.write_text("".join(json.dumps(job) + "\n" for job in batch))
            for entry in ("run.py", "tokenify_pilot"):
                with self.subTest(entry=entry):
                    output = Path(tmp) / (entry + ".jsonl")
                    result = run_command(entry, "judge-legacy", "--jobs", str(jobs_path),
                                         "--model", "unused-model", "--out", str(output))
                    self.assertTrue(result["dry_run"])
                    self.assertEqual(result["jobs"], 90)
                    self.assertEqual(result["limit"], 1)
                    self.assertFalse(output.exists())
if __name__=="__main__":unittest.main()
