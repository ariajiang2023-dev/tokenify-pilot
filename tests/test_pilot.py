import json
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import run
from pilot_lib import read, messages

class PilotTests(unittest.TestCase):
    def test_historical_audit(self):
        self.assertEqual(run.audit()["status_counts"],{"ok":74,"invalid_label":16})
        self.assertEqual(run.audit()["invalid_reasons"],{
            "missing_required_fields":6,"leading_space_in_rationale_key":5,"invalid_json":5})

    def test_job_reversal_and_blinding(self):
        req=read(run.ROOT/"data/pilot_requests.jsonl")
        res=read(run.ROOT/"data/pilot_responses.jsonl")
        batch=list(run.jobs(req,res))
        self.assertEqual(len(batch),90)
        self.assertEqual(len({j["job_id"] for j in batch}),90)
        for a,b in zip(batch[::2],batch[1::2]):
            self.assertEqual(a["payload"]["reference_senses"],b["payload"]["reference_senses"][::-1])
            self.assertEqual(a["payload"]["answer"],b["payload"]["answer"])
            self.assertNotIn("gpt-5.6-sol",json.dumps(a["payload"]))
            self.assertNotIn("reviewer",json.dumps(a["payload"]))

    def test_messages_match_archive(self):
        cues=json.loads((run.ROOT/"data/cues_v1.json").read_text())
        for q in read(run.ROOT/"data/pilot_requests.jsonl"):
            self.assertEqual(messages(q["question"],q["condition"],q["cue_field"],cues.get(q["cue_field"])),q["messages"])

    def test_invalid_is_not_zero(self):
        j=next(run.jobs(read(run.ROOT/"data/pilot_requests.jsonl"),read(run.ROOT/"data/pilot_responses.jsonl")))
        self.assertEqual(run.check("not json",j),("invalid_label",None))
        self.assertEqual(run.check("{}",j),("invalid_label",None))

    def test_reference_ids_stable(self):
        qs=read(run.ROOT/"data/pilot_requests.jsonl")
        self.assertEqual(sum(q["condition"]=="C0" for q in qs),9)
        for q in qs:
            self.assertEqual(len({r["sense_id"] for r in q["references"]}),2)
            self.assertTrue(all(r["url"].startswith("https://") for r in q["references"]))
if __name__=="__main__":unittest.main()
