"""Portable analysis unit checks; research workbooks are tested separately."""

import copy
import csv
import hashlib
import importlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import openpyxl
from tokenify_analysis import expertqa_reference_prep as prep
from tokenify_analysis import judge_audit as audit
from tests.analysis_fixtures import fixture_tables, packet_fixture, workbook_copy

ROOT = Path(__file__).resolve().parents[1]
ANALYSIS = ROOT / "inputs/Tokenify_analysis_plan.xlsx"
BLIND = ROOT / "inputs/Tokenify_blind_review_SIMPLE.xlsx"

class ReferencePrepTests(unittest.TestCase):
    def test_match_uses_whole_question_not_suffix(self):
        records = [{'question': 'First question?'}, {'question': 'Another question?'}]
        self.assertEqual(prep.match_question(' First\nquestion? ', records), 0)
        with self.assertRaises(ValueError):
            prep.match_question('question?', records)

    def test_duplicate_and_missing_questions_fail(self):
        with self.assertRaises(ValueError):
            prep.match_question('Q?', [{'question': 'Q?'}, {'question': ' Q? '}])
        with self.assertRaises(ValueError):
            prep.match_question('Absent?', [{'question': 'Q?'}])

    def test_empty_reference_is_not_replaced_with_model_answer(self):
        row = {'answers': {'gpt4': {'answer_string': 'A generated answer', 'revised_answer_string': ''}}}
        with self.assertRaises(ValueError):
            prep.extract_reference(row)

    def test_source_digest_rejects_modified_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'data.jsonl'
            path.write_text('{"question":"changed"}\n')
            with self.assertRaises(ValueError):
                prep.load_verified_source(path)

    def test_blank_workbook_labels_are_not_filled_from_recommendations(self):
        rows = [prep._read_workbook_labels((None,) * 11, n) for n in range(2, 32)]
        self.assertEqual(len(rows), 30)
        for row in rows:
            self.assertIsNone(row['human_correct'])
            self.assertIsNone(row['human_change_vs_c0'])
            self.assertIsNone(row['human_field_mentioned_or_addressed'])
            self.assertIsNone(row['human_stereotyped'])
            self.assertEqual(row['human_label_status'], 'not_recorded')

    def test_zero_and_partial_labels_remain_distinct_from_blank(self):
        values = (None,) * 6 + (0, None, 'No', None, 'A recorded note <kept verbatim>.')
        row = prep._read_workbook_labels(values, 2)
        self.assertEqual(row['human_correct'], 0)
        self.assertIsNone(row['human_change_vs_c0'])
        self.assertEqual(row['human_field_mentioned_or_addressed'], 'No')
        self.assertIsNone(row['human_stereotyped'])
        self.assertEqual(row['human_notes'], values[10])
        self.assertEqual(row['human_label_status'], 'partial_workbook_labels')
        packet = packet_fixture()
        packet['scoring_recommendations'][0].update(row)
        rendered = prep.render_html(packet)
        self.assertIn('Final workbook correctness</dt><dd>0', rendered)
        self.assertIn('&lt;kept verbatim&gt;', rendered)

    def test_invalid_recorded_binary_label_is_rejected(self):
        for value in (2, '0', False):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, 'Invalid workbook correctness'):
                    prep._read_workbook_labels((None,) * 6 + (value, None, None, None, None), 2)

    def test_accepts_all_workbook_change_categories_verbatim(self):
        for category in ('none', 'examples or difficulty only', 'drops a key point', 'contradicts a key point'):
            with self.subTest(category=category):
                row = prep._read_workbook_labels((None,) * 6 + (0, category, 'No', 'No', 'Recorded note.'), 2)
                self.assertEqual(row['human_change_vs_c0'], category)
                self.assertEqual(row['human_label_status'], 'complete_workbook_labels')

    def test_shortened_change_category_is_not_a_workbook_label(self):
        with self.assertRaisesRegex(ValueError, 'Invalid workbook change category'):
            prep._read_workbook_labels((None,) * 6 + (0, 'contradicts', 'No', 'No', None), 2)

    def test_html_escapes_response_text_and_has_thirty_review_cards(self):
        packet = packet_fixture()
        packet['scoring_recommendations'][0]['response_raw'] = '<script>bad()</script>'
        rendered = prep.render_html(packet)
        self.assertEqual(rendered.count('<details>'), 30)
        self.assertIn('&lt;script&gt;bad()&lt;/script&gt;', rendered)
        self.assertNotIn('<script>', rendered)

    def test_only_concrete_field_addresses_are_yes(self):
        packet = packet_fixture()
        positives = [r for r in packet['scoring_recommendations'] if r['field_addressed_recommendation'] == 'Yes']
        self.assertEqual({r['response_id'] for r in positives}, {'E04', 'E12', 'E16', 'E20'})
        self.assertTrue(all((r['field_address_evidence'] for r in positives)))
        combined = [r for r in packet['scoring_recommendations'] if r['field_mentioned_or_addressed_recommendation'] == 'Yes']
        self.assertEqual({r['response_id'] for r in combined}, {'E04', 'E12', 'E14', 'E15', 'E16', 'E20'})
