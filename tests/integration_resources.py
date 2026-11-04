"""Public-source integration checks; run prepare_resources.py first. No Box files required."""
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
ANALYSIS = ROOT / 'inputs/Tokenify_analysis_plan.xlsx'
BLIND = ROOT / 'inputs/Tokenify_blind_review_SIMPLE.xlsx'
from tests.test_analysis_judge_audit import fixture

class MetricsResourceTests(unittest.TestCase):

    def metrics(self):
        self.assertTrue((ROOT / 'tokenify_analysis' / 'metrics.py').exists(), 'metrics.py has not been implemented yet')
        return importlib.import_module('tokenify_analysis.metrics')

    def test_fk_matches_reference_fixture_and_pinned_textstat(self):
        m = self.metrics()
        raw = 'The cat sat on the mat. The dog ran to the park.'
        result = m.measure_text(raw)
        self.assertIn('reading_grade_fk', result)
        self.assertAlmostEqual(result['reading_grade_fk'], -1.45)
        import textstat
        textstat.set_lang('en_US')
        textstat.set_rounding_points(None)
        self.assertEqual(result['reading_grade_fk'], textstat.flesch_kincaid_grade(m.plain_text(raw)))
        self.assertEqual(result['fk_n_words'], 12)
        self.assertEqual(result['fk_n_sentences'], 2)
        self.assertEqual(result['fk_n_syllables'], 12)
        self.assertEqual(result['fk_n_cmudict_words'], 12)
        self.assertEqual(result['fk_n_pyphen_fallback_words'], 0)

    def test_fk_reports_out_of_dictionary_pyphen_fallback(self):
        m = self.metrics()
        result = m.measure_text('The quizzaciousnotaword sits on the mat.')
        self.assertIn('fk_n_pyphen_fallback_words', result)
        self.assertEqual(result['fk_n_pyphen_fallback_words'], 1)
        self.assertEqual(result['fk_n_cmudict_words'], 5)

    def test_fk_uses_local_resources_without_download(self):
        m = self.metrics()
        import nltk
        from unittest.mock import patch
        with patch.object(nltk, 'download', side_effect=AssertionError('runtime must stay offline')):
            result = m.measure_text('A reader can use a local dictionary for this sentence.')
        self.assertIn('reading_grade_fk', result)
        self.assertIsInstance(result['reading_grade_fk'], float)

class ReferencePrepResourceTests(unittest.TestCase):

    def test_real_source_maps_all_nine_and_preserves_authors(self):
        rows = prep.load_verified_source(prep.DEFAULT_SOURCE)
        self.assertEqual(len(rows), 2177)
        for item in prep.QUESTION_KEYS:
            index = prep.match_question(item['question'], rows)
            self.assertEqual(index, item['eqa_index'])
            self.assertEqual(rows[index]['annotator_id'], item['author_id'])
            self.assertTrue(prep.extract_reference(rows[index])['revised_answer_string'])
