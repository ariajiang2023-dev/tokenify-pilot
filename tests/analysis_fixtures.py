"""Synthetic examples for public unit tests; no workbook annotations included."""

from contextlib import contextmanager
from pathlib import Path
import tempfile

import openpyxl

from tokenify_analysis import expertqa_reference_prep as prep


def fixture_tables():
    review = []
    for number, target in enumerate(("both", "A", "B"), 1):
        review.append({"#": f"S{number:02}", "Term": "example", "Meaning A": "computer science: A sample technical meaning.",
                       "Meaning B": "education/psychology: A sample learning meaning.", "target": target,
                       "What the student told the model": "Synthetic field cue", "Model's answer": "A synthetic response.", "_source_row": number + 1})
    agreement = []
    for number, (response, target, condition) in enumerate((("S01", "A", "C0"), ("S01", "B", "C0"), ("S02", "A", "C1"), ("S03", "B", "C1")), 1):
        agreement.append({"judgment": f"J{number:02}", "row #": response, "term": "example", "target": target, "condition": condition})
    eqa = []
    for number, condition in enumerate(("C0", "C1"), 1):
        eqa.append({"#": f"E{number:02}", "question": "What is an example?", "condition": condition,
                    "what was sent": ("user: " if condition == "C0" else "user: My academic field is computer science. ") + "What is an example?",
                    "model's answer": "A synthetic example answer.", "_source_row": number + 1})
    return {
        "Review": review, "Agreement": agreement, "ExpertQA_scoring": eqa,
        "ExpertQA_Sample": [{"eqa_index": 1, "question": "What is an example?", "mapped_discipline": "CS", "field": "Computer Science", "include": "Suggested"}],
        "Terms": [{"term_id": "T01", "term": "example", "field A": "CS", "field B": "Ed/Psych", "verified?": "No"}],
        "Contexts": [{"term_id": "T01", "term": "example", "question (asked on its own)": "What is an example?",
                      **{f"context {n}": f"Synthetic context {n}." for n in range(1, 6)}, "screened by (1)": None, "screened by (2)": None}],
    }


def packet_fixture():
    references = [{"pilot_question": n, "question": f"Synthetic question {n}?", "eqa_index": n,
                   "source_line_1based": n + 1, "question_author_id": f"synthetic-{n}", "answer_validator_id": "synthetic",
                   "source_answer_model": "synthetic", "revised_answer_string": "A synthetic reference answer.",
                   "key_points": ["A synthetic key point."]} for n in range(1, 10)]
    rows = []
    for number in range(1, 31):
        response_id = f"E{number:02}"
        rows.append({"response_id": response_id, "pilot_question": (number - 1) % 9 + 1, "condition": "C0", "workbook_row": number + 1,
                     "correct_recommendation": 1, "change_vs_c0_recommendation": "none", "field_addressed_recommendation": "Yes" if response_id in prep.FIELD_ADDRESS_EVIDENCE else "No",
                     "field_address_evidence": prep.FIELD_ADDRESS_EVIDENCE.get(response_id, ""),
                     "explicit_cue_field_mention_evidence": prep.EXPLICIT_CUE_EVIDENCE.get(response_id, ""),
                     "field_mentioned_or_addressed_recommendation": "Yes" if response_id in prep.FIELD_ADDRESS_EVIDENCE or response_id in prep.EXPLICIT_CUE_EVIDENCE else "No",
                     "stereotyped_recommendation": "No", "notes": "Synthetic test note.", "label_origin": "Synthetic test fixture",
                     "messages_raw": "Synthetic prompt.", "response_raw": "Synthetic answer.",
                     **prep._read_workbook_labels((response_id, None, None, None, None, None, None, None, None, None, None), number + 1)})
    return {"references": references, "scoring_recommendations": rows,
            "workbook_label_readback": {"complete_rows": 0, "partial_rows": 0, "unrecorded_rows": 30}, "workbook_review_provenance": []}


@contextmanager
def workbook_copy(updates, overview_note=None):
    """Explicit integration helper: modify only a disposable workbook copy."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "labels-copy.xlsx"
        wb = openpyxl.load_workbook(prep.DEFAULT_WORKBOOK)
        for cell, value in updates.items():
            wb["ExpertQA_scoring"][cell] = value
        if overview_note is not None:
            wb["START_HERE"]["A10"] = overview_note
        wb.save(path)
        wb.close()
        yield path
