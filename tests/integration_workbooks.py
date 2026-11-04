"""Explicit research integration checks. Requires prepared resources and local Box input copies."""
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

class MetricsIntegrationTests(unittest.TestCase):

    def metrics(self):
        self.assertTrue((ROOT / 'tokenify_analysis' / 'metrics.py').exists(), 'metrics.py has not been implemented yet')
        return importlib.import_module('tokenify_analysis.metrics')

    def test_extracts_every_pilot_response_and_preserves_raw_text(self):
        m = self.metrics()
        tables = m.load_sources(ANALYSIS, BLIND)
        responses = m.extract_responses(tables)
        self.assertEqual(len(responses), 75)
        self.assertEqual(sum((r['dataset'] == 'Review' for r in responses)), 45)
        self.assertEqual(sum((r['dataset'] == 'ExpertQA_scoring' for r in responses)), 30)
        self.assertEqual(len({r['response_id'] for r in responses}), 75)
        by_id = {r['response_id']: r for r in responses}
        self.assertEqual(by_id['S01']['response_text_raw'], tables['Review'][0]["Model's answer"])
        self.assertEqual(by_id['S01']['condition'], 'C0')
        self.assertEqual(by_id['S02']['cue_field'], 'CS')
        self.assertIsNone(by_id['S01']['question'])

    def test_recovers_full_question_and_retains_truncated_column(self):
        m = self.metrics()
        rows = {r['response_id']: r for r in m.extract_responses(m.load_sources(ANALYSIS, BLIND))}
        self.assertEqual(rows['E09']['question_raw'], 'your classroom?')
        self.assertTrue(rows['E09']['question'].startswith('If you are a mathematics teacher'))
        self.assertEqual(rows['E09']['eqa_index'], 177)
        self.assertIn('question_column_truncated', rows['E09']['quality_flags'])
        self.assertIn('raw_generation_unverified', rows['E12']['quality_flags'])
        self.assertIn('known_page_number_artifact_in_response', rows['E12']['quality_flags'])
        self.assertIn('14', rows['E12']['response_text_raw'])

    def test_unresolved_source_ids_are_explicit_and_strict_mode_rejects(self):
        m = self.metrics()
        tables = m.load_sources(ANALYSIS, BLIND)
        rows = m.extract_responses(tables)
        unresolved = {r['response_id'] for r in rows if r['source_join_status'] == 'unmatched_source_question'}
        self.assertEqual(unresolved, {'E05', 'E06', 'E07', 'E08', 'E29', 'E30'})
        self.assertTrue(all((r['eqa_index'] is None for r in rows if r['response_id'] in unresolved)))
        with self.assertRaisesRegex(ValueError, 'incomplete ExpertQA'):
            m.extract_responses(tables, require_complete_eqa_join=True)

    def test_full_inventory_counts_slots_active_terms_fields_and_inclusion(self):
        m = self.metrics()
        inventory = m.build_inventory(m.load_sources(ANALYSIS, BLIND))
        self.assertEqual(inventory['terms']['slots'], 60)
        self.assertEqual(inventory['terms']['active_terms'], 23)
        self.assertEqual(inventory['terms']['complete_field_pairs'], 14)
        self.assertEqual(inventory['contexts']['available_questions_and_contexts'], 138)
        self.assertEqual(inventory['contexts']['available_standalone_questions'], 23)
        self.assertEqual(inventory['contexts']['available_contexts'], 115)
        self.assertIn('available_items_without_field_assignment', inventory['contexts'])
        self.assertEqual(inventory['contexts']['available_items_without_field_assignment'], 54)
        self.assertEqual(inventory['contexts']['available_items_by_field_membership_nonexclusive'], {field: count * 6 for field, count in inventory['terms']['by_field_membership'].items()})
        self.assertEqual(inventory['expertqa_sample']['candidate_items'], 688)
        self.assertEqual(inventory['expertqa_sample']['by_mapped_discipline'], {'CS': 97, 'EDPSY': 202, 'HLTH': 189, 'CONTROL': 200})
        self.assertEqual(inventory['expertqa_sample']['by_include'], {'Suggested': 423, 'TBD': 170, 'No': 95})
        self.assertEqual(inventory['expertqa_sample']['approved_items'], 0)

    def test_export_writes_only_csv_json_and_leaves_workbooks_unchanged(self):
        m = self.metrics()
        before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in (ANALYSIS, BLIND)}
        with tempfile.TemporaryDirectory() as temp:
            result = m.run_analysis(ANALYSIS, BLIND, Path(temp))
            files = list(Path(temp).iterdir())
            self.assertTrue(files)
            self.assertTrue(all((p.suffix in {'.csv', '.json'} for p in files)))
            self.assertEqual(result['response_count'], 75)
            raw = json.loads((Path(temp) / 'pilot_responses.json').read_text())
            self.assertEqual(len(raw), 75)
            metadata = json.loads((Path(temp) / 'metrics_metadata.json').read_text())
            self.assertEqual(metadata['unresolved_expertqa_response_count'], 6)
            self.assertIn('4.71', metadata['methodology']['reading_grade_ari'])
            self.assertIn('reading_grade_fk', metadata['methodology'])
            self.assertEqual(metadata['runtime']['dependencies']['textstat'], '0.7.13')
            self.assertEqual(metadata['runtime']['dependencies']['pyphen'], '0.18.1')
            self.assertEqual(metadata['runtime']['dependencies']['nltk'], '3.10.4')
            self.assertEqual(metadata['runtime']['fk_language'], 'en_US')
            self.assertEqual(metadata['runtime']['cmudict']['dictionary_sha256'], 'cad209c39eb87677d64e93d97f8eed10b7e6f9bdd42de8e7ca8efc8e17d62e8a')
        self.assertEqual(before, {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in (ANALYSIS, BLIND)})

class AuditIntegrationTests(unittest.TestCase):

    def test_workbook_rejects_omitted_c0_target(self):
        root = Path(__file__).resolve().parents[1] / 'inputs'
        with zipfile.ZipFile(root / 'Qwen_pilot_run_records.zip') as z:
            mapping = json.loads(z.read('results.json'))['mapping']
        incomplete = [m for m in mapping if m['row'] != 3]
        with self.assertRaisesRegex(ValueError, 'complete workbook'):
            audit.workbook_humans(root / 'Tokenify_blind_review_SIMPLE.xlsx', incomplete)

class ReferencePrepIntegrationTests(unittest.TestCase):

    def test_build_does_not_change_workbook_and_reads_actual_labels(self):
        before = hashlib.sha256(prep.DEFAULT_WORKBOOK.read_bytes()).hexdigest()
        packet = prep.build_packet(prep.DEFAULT_WORKBOOK, prep.DEFAULT_SOURCE)
        self.assertEqual(len(packet['references']), 9)
        self.assertEqual(len(packet['scoring_recommendations']), 30)
        self.assertEqual(len({x['question_author_id'] for x in packet['references']}), 9)
        self.assertIn('workbook_label_readback', packet)
        wb = openpyxl.load_workbook(prep.DEFAULT_WORKBOOK, read_only=True, data_only=False)
        try:
            for row in packet['scoring_recommendations']:
                n = row['workbook_row']
                for field, column in (('human_correct', 'G'), ('human_change_vs_c0', 'H'), ('human_field_mentioned_or_addressed', 'I'), ('human_stereotyped', 'J'), ('human_notes', 'K')):
                    self.assertEqual(row[field], wb['ExpertQA_scoring'][f'{column}{n}'].value)
                self.assertTrue(row['label_origin'].startswith('AI-assisted draft'))
        finally:
            wb.close()
        self.assertTrue(all((x['correct_recommendation'] == 1 for x in packet['scoring_recommendations'])))
        self.assertEqual(before, hashlib.sha256(prep.DEFAULT_WORKBOOK.read_bytes()).hexdigest())

    def test_all_thirty_recorded_labels_and_provenance_are_exported(self):
        positives = {'E04', 'E12', 'E14', 'E15', 'E16', 'E20'}
        notes = 'James personally reviewed and confirmed the ExpertQA pilot labels, Nov 4. Separate from future independent validation.'
        updates = {}
        for n in range(2, 32):
            response_id = f'E{n - 1:02}'
            updates.update({f'G{n}': 1, f'H{n}': 'none', f'I{n}': 'Yes' if response_id in positives else 'No', f'J{n}': 'No', f'K{n}': notes})
        with workbook_copy(updates, overview_note=notes) as path:
            packet = prep.build_packet(path, prep.DEFAULT_SOURCE)
        self.assertTrue('workbook_review_provenance' in packet, 'Workbook confirmation notes must be retained')
        self.assertIn({'sheet': 'START_HERE', 'cell': 'A10', 'text': notes}, packet['workbook_review_provenance'])
        self.assertEqual(packet['workbook_label_readback']['complete_rows'], 30)
        self.assertEqual(packet['workbook_label_readback']['missing_correctness_rows'], 0)
        self.assertEqual(packet['workbook_label_readback']['columns']['human_correct'], 'G')
        for row in packet['scoring_recommendations']:
            self.assertEqual(row['human_label_status'], 'complete_workbook_labels')
            self.assertEqual(row['human_notes'], notes)
            self.assertNotIn('pending', row['label_origin'])
            self.assertFalse(row['needs_specific_human_decision'])
        rendered = prep.render_html(packet)
        self.assertIn('30 of 30 rows have complete workbook labels', rendered)
        self.assertNotIn('No human-confirmation fields have been completed', rendered)
        self.assertIn(notes, rendered)
        with tempfile.TemporaryDirectory() as tmp:
            prep.write_packet(packet, Path(tmp))
            with (Path(tmp) / 'scoring_recommendations.csv').open() as handle:
                records = list(csv.DictReader(handle))
        self.assertEqual(len(records), 30)
        self.assertEqual({r['response_id'] for r in records if r['human_field_mentioned_or_addressed'] == 'Yes'}, positives)
        self.assertTrue(all((r['human_correct'] == '1' and r['human_notes'] == notes for r in records)))

    def test_changed_response_cannot_inherit_existing_draft(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'modified-copy.xlsx'
            wb = openpyxl.load_workbook(prep.DEFAULT_WORKBOOK)
            wb['ExpertQA_scoring']['E2'] = 'A changed response requiring new review'
            wb.save(path)
            wb.close()
            with self.assertRaisesRegex(ValueError, 'Draft annotation input changed for E01'):
                prep.build_packet(path, prep.DEFAULT_SOURCE)

    def test_human_label_changes_do_not_invalidate_response_bound_drafts(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'human-label-copy.xlsx'
            wb = openpyxl.load_workbook(prep.DEFAULT_WORKBOOK)
            wb['Qwen_check']['H16'] = 0
            wb.save(path)
            wb.close()
            packet = prep.build_packet(path, prep.DEFAULT_SOURCE)
            self.assertEqual(len(packet['scoring_recommendations']), 30)
