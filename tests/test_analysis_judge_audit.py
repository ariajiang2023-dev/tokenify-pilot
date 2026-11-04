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

def fixture():
    judgments = []
    for item, choice in [('S01', 'A'), ('S02', 'multiple')]:
        for order in ['AB', 'BA']:
            displayed = choice if order == 'AB' or choice not in 'AB' else 'B'
            judgments.append({'id': item, 'order': order, 'selected': choice, 'mentioned': {'A': True, 'B': False}, 'status': 'ok', 'rationale': 'Evidence.', 'attempts': [{'message': {'content': __import__('json').dumps({'choice': displayed, 'mentioned_A': order == 'AB', 'mentioned_B': order == 'BA', 'rationale': 'Evidence.'})}}]})
    return {'judgments': judgments, 'mapping': [{'row': 2, 'id': 'S01', 'target': 'A'}, {'row': 3, 'id': 'S01', 'target': 'B'}, {'row': 4, 'id': 'S02', 'target': 'A'}]}

class AuditTests(unittest.TestCase):
    def test_binary_kappa_known_example(self):
        result = audit.agreement([0, 0, 1, 1], [0, 1, 1, 1])
        self.assertEqual(result['n'], 4)
        self.assertAlmostEqual(result['agreement'], 0.75)
        self.assertAlmostEqual(result['kappa'], 0.5)

    def test_constant_labels_have_undefined_kappa(self):
        self.assertIsNone(audit.agreement([1, 1], [1, 1])['kappa'])

    def test_missing_labels_are_not_zero(self):
        with self.assertRaises(ValueError):
            audit.agreement([0, None], [0, 0])

    def test_order_mapping_uses_original_sense(self):
        result = audit.audit_records(fixture(), {2: 1, 3: 0, 4: 0})
        self.assertEqual(result['target_order_consistent'], 3)
        self.assertEqual(result['ab_vs_available_human']['agreement'], 1)
        self.assertEqual(result['ba_vs_available_human']['agreement'], 1)

    def test_missing_adjudication_prevents_all_row_claim(self):
        result = audit.audit_records(fixture(), {2: 1, 3: None, 4: 0})
        self.assertEqual(result['human_labels_available'], 2)
        self.assertIsNone(result['all_rows_kappa_ab'])
        self.assertEqual(result['pilot_gate_status'], 'pending_human_labels')

    def test_ninety_percent_does_not_silently_become_a_hundred(self):
        data = {'judgments': [], 'mapping': []}
        for n in range(10):
            item = f'S{n:02d}'
            pair = copy.deepcopy(fixture()['judgments'][:2])
            for j in pair:
                j['id'] = item
            if n == 9:
                pair[1]['selected'] = 'B'
                pair[1]['attempts'][0]['message']['content'] = '{"choice":"A","mentioned_A":false,"mentioned_B":true,"rationale":"Evidence."}'
            data['judgments'].extend(pair)
            data['mapping'].append({'row': n + 2, 'id': item, 'target': 'A'})
        result = audit.audit_records(data, {n + 2: n % 2 for n in range(10)})
        self.assertTrue(result['order_threshold_90_percent_met'])
        self.assertEqual(result['pilot_gate_status'], 'pending_order_conflict_policy')

    def test_order_conflict_is_reported_not_overwritten(self):
        data = fixture()
        bad = data['judgments'][1]
        bad['selected'] = 'B'
        bad['attempts'][0]['message']['content'] = '{"choice":"A","mentioned_A":false,"mentioned_B":true,"rationale":"Evidence."}'
        bad['mentioned'] = {'A': True, 'B': False}
        result = audit.audit_records(data, {2: 1, 3: 0, 4: 0})
        self.assertEqual(result['target_order_disagreements'], 2)
        self.assertEqual(result['choice_order_disagreement_ids'], ['S01'])

    def test_duplicate_requests_fail(self):
        data = fixture()
        data['judgments'].append(copy.deepcopy(data['judgments'][0]))
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            audit.audit_records(data, {2: 1, 3: 0, 4: 0})

    def test_missing_order_fails(self):
        data = fixture()
        data['judgments'].pop()
        with self.assertRaisesRegex(ValueError, 'orders'):
            audit.audit_records(data, {2: 1, 3: 0, 4: 0})

    def test_status_failure_never_becomes_zero(self):
        data = fixture()
        data['judgments'][0]['status'] = 'timeout'
        with self.assertRaisesRegex(ValueError, 'status'):
            audit.audit_records(data, {2: 1, 3: 0, 4: 0})

    def test_mismatched_normalized_choice_fails(self):
        data = fixture()
        data['judgments'][1]['selected'] = 'B'
        with self.assertRaisesRegex(ValueError, 'normaliz'):
            audit.audit_records(data, {2: 1, 3: 0, 4: 0})

    def test_empty_input_fails(self):
        with self.assertRaises(ValueError):
            audit.audit_records({'judgments': [], 'mapping': []}, {})
