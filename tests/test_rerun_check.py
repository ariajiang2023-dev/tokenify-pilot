import copy
import unittest

from tokenify_analysis.rerun_check import assess_scores, check_identity


def rows():
    return [dict(row=i + 2, judgment=f'J{i+1:02}', response_id=f'S{i+1:02}',
                 target='A', human=i % 2, AB=i % 2, BA=i % 2,
                 archive_AB=i % 2, archive_BA=i % 2) for i in range(54)]


class RerunCheckTests(unittest.TestCase):
    def test_score_identity_includes_judgment_id(self):
        expected = ('J01', 'S01', 'A')
        check_identity(expected, expected, 2)
        for changed in [('WRONG-ID', 'S01', 'A'), ('J01', 'S02', 'A'), ('J01', 'S01', 'B')]:
            with self.subTest(changed=changed), self.assertRaisesRegex(ValueError, 'identity mismatch'):
                check_identity(changed, expected, 2)

    def test_identical_scores_need_run_evidence(self):
        result = assess_scores(rows())
        self.assertEqual(result['order_matches'], 54)
        self.assertEqual(result['all_rows_kappa_ab'], 1)
        self.assertEqual(result['status'], 'rerun_evidence_missing')
        self.assertTrue(result['identical_to_archived_scores'])

    def test_49_matches_pass_order_threshold_but_48_fail(self):
        data = rows()
        for item in data[:5]:
            item['BA'] = 1 - item['BA']
        result = assess_scores(data, run_id='rerun-2', prompt_sha256='a' * 64)
        self.assertTrue(result['order_threshold_met'])
        self.assertTrue(result['conservative_all_row_kappa_threshold_met'])
        self.assertEqual(result['status'], 'numeric_thresholds_met_review_raw_run')
        data[5]['BA'] = 1 - data[5]['BA']
        self.assertFalse(assess_scores(data)['order_threshold_met'])

    def test_inconsistent_rows_are_not_removed_from_kappa(self):
        data = rows()
        for item in data[:20]:
            item['AB'] = 1 - item['AB']
            item['BA'] = item['AB']
        result = assess_scores(data, run_id='rerun-2', prompt_sha256='a' * 64)
        self.assertEqual(result['human_rows'], 54)
        self.assertTrue(result['order_threshold_met'])
        self.assertFalse(result['conservative_all_row_kappa_threshold_met'])
        self.assertEqual(result['status'], 'fails_kappa_threshold')

    def test_incomplete_and_invalid_values_are_rejected(self):
        for edit in ('short', 'missing', 'bool', 'duplicate', 'target'):
            data = rows()
            if edit == 'short': data.pop()
            elif edit == 'missing': data[0]['AB'] = None
            elif edit == 'bool': data[0]['human'] = True
            elif edit == 'duplicate': data[1]['judgment'] = data[0]['judgment']
            else: data[0]['target'] = 'both'
            with self.subTest(edit=edit), self.assertRaises(ValueError):
                assess_scores(data)

    def test_run_metadata_must_be_complete(self):
        for kwargs in ({'run_id': 'x'}, {'prompt_sha256': 'a' * 64},
                       {'run_id': 'x', 'prompt_sha256': 'not-a-hash'}):
            with self.assertRaises(ValueError):
                assess_scores(rows(), **kwargs)

    def test_does_not_mutate_input(self):
        data = rows(); original = copy.deepcopy(data)
        assess_scores(data)
        self.assertEqual(data, original)


if __name__ == '__main__':
    unittest.main()
