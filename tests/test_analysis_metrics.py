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

class MetricsTests(unittest.TestCase):
    def metrics(self):
        self.assertTrue((ROOT / 'tokenify_analysis' / 'metrics.py').exists(), 'metrics.py has not been implemented yet')
        return importlib.import_module('tokenify_analysis.metrics')

    def test_unicode_word_rule_handles_accents_connectors_and_symbols(self):
        m = self.metrics()
        self.assertEqual(m.unicode_words("Café café don't learner’s noise-induced 80% x_y 中文"), ['Café', 'café', "don't", 'learner’s', 'noise-induced', '80', 'x', 'y', '中文'])

    def test_plain_text_keeps_numeric_content_and_link_labels(self):
        m = self.metrics()
        raw = '## **Recall**\\n- [Memory](https://example.com) is `useful`. 14\n\\[x=80\\]\n'
        plain = m.plain_text(raw)
        self.assertEqual(m.unicode_words(plain), ['Recall', 'Memory', 'is', 'useful', '14'])
        self.assertIn('14', plain)
        self.assertNotIn('example', plain)

    def test_missing_or_nonprose_response_is_unavailable(self):
        m = self.metrics()
        for raw in (None, '', ' \n', '***', '\\[x=80\\]'):
            with self.subTest(raw=raw):
                result = m.measure_text(raw)
                self.assertIsNone(result['n_words'])
                self.assertIsNone(result['reading_grade_ari'])
                self.assertEqual(result['metric_status'], 'unavailable')

    def test_ari_matches_published_formula_without_clamping(self):
        m = self.metrics()
        result = self.measure_without_resources('The cat sat. The dog ran.')
        self.assertEqual(result['n_words'], 6)
        self.assertEqual(result['n_alphanumeric_characters'], 18)
        self.assertEqual(result['n_sentence_units'], 2)
        self.assertAlmostEqual(result['reading_grade_ari'], 4.71 * 18 / 6 + 0.5 * 6 / 2 - 21.43)

    def test_fk_marks_missing_text_unavailable(self):
        m = self.metrics()
        for raw in (None, '', '***', '\\[x=80\\]'):
            with self.subTest(raw=raw):
                result = m.measure_text(raw)
                self.assertIn('reading_grade_fk', result)
                for key in ('reading_grade_fk', 'fk_n_words', 'fk_n_sentences', 'fk_n_syllables'):
                    self.assertIsNone(result[key])

    def test_newline_fragments_count_as_documented_sentence_units(self):
        m = self.metrics()
        self.assertEqual(self.measure_without_resources('A heading\nAn item\nAnother item.')['n_sentence_units'], 3)

    def test_duplicate_response_id_is_rejected(self):
        m = self.metrics()
        tables = fixture_tables()
        tables['Review'].append(copy.deepcopy(tables['Review'][0]))
        with self.assertRaisesRegex(ValueError, 'duplicate response ID'):
            m.extract_responses(tables)

    def test_missing_agreement_join_is_rejected(self):
        m = self.metrics()
        tables = fixture_tables()
        tables['Agreement'] = [r for r in tables['Agreement'] if r['row #'] != 'S02']
        with self.assertRaisesRegex(ValueError, 'incomplete Review/Agreement'):
            m.extract_responses(tables)

    def test_conflicting_agreement_condition_is_rejected(self):
        m = self.metrics()
        tables = fixture_tables()
        tables['Agreement'][1]['condition'] = 'C1'
        with self.assertRaisesRegex(ValueError, 'conflicting Agreement'):
            m.extract_responses(tables)

    def test_duplicate_expertqa_source_id_is_rejected(self):
        m = self.metrics()
        tables = fixture_tables()
        tables['ExpertQA_Sample'].append(copy.deepcopy(tables['ExpertQA_Sample'][0]))
        with self.assertRaisesRegex(ValueError, 'duplicate ExpertQA'):
            m.extract_responses(tables)

    def test_mismatched_question_within_condition_group_is_rejected(self):
        m = self.metrics()
        tables = fixture_tables()
        tables['ExpertQA_scoring'][1]['question'] = 'An unrelated question?'
        with self.assertRaisesRegex(ValueError, 'question mismatch'):
            m.extract_responses(tables)

    def test_incomplete_term_context_join_is_rejected(self):
        m = self.metrics()
        tables = fixture_tables()
        tables['Contexts'] = tables['Contexts'][1:]
        with self.assertRaisesRegex(ValueError, 'incomplete Terms/Contexts'):
            m.build_inventory(tables)

    def test_summaries_exclude_unavailable_metrics_from_denominators(self):
        m = self.metrics()
        rows = [{'dataset': 'demo', 'condition': 'C0', 'field': 'CS', **self.measure_without_resources('The cat sat.')}, {'dataset': 'demo', 'condition': 'C0', 'field': 'CS', **self.measure_without_resources(None)}]
        rows[0]['reading_grade_fk'] = 1.0
        summary = m.summarize(rows)
        overall = next((r for r in summary if r['group_type'] == 'dataset'))
        self.assertEqual(overall['n_responses'], 2)
        self.assertEqual(overall['n_available_word_counts'], 1)
        self.assertEqual(overall['mean_words'], 3)
        self.assertEqual(overall['n_missing_word_counts'], 1)
        self.assertIn('mean_reading_grade_fk', overall)
        self.assertEqual(overall['n_available_reading_grades_fk'], 1)

    def measure_without_resources(self, text):
        m = self.metrics()
        with patch.object(m, '_fk_measure', return_value={}):
            return m.measure_text(text)
