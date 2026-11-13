"""Synthetic checks for full-run analysis; these are not research results."""
import copy
import csv
import io
import json
from pathlib import Path
import tempfile
import unittest

from tokenify_analysis import full_run as fr
from tokenify_analysis import full_run_io as fio
from tokenify_analysis import validation_sample as vs


def response(rid, term='t1', condition='C0', target='both', label='A', **kw):
    row = dict(response_id=rid, request_id=rid, model='m1', set='terms',
               item_id=term + ':0', term_id=term, context_index=0,
               condition=condition, target=target, cue_field=None if condition=='C0' else ('CS' if target=='A' else 'STAT'),
               field_a='CS', field_b='STAT', question='What does this term mean?',
               reference_a='First meaning.', reference_b='Second meaning.',
               response_text='A short answer.', response_phase='first', repeat_index=0,
               split='main', judge_ab=label, judge_ba=label,
               mentioned_a_ab=True, mentioned_a_ba=True,
               mentioned_b_ab=False, mentioned_b_ba=False,
               n_words=3, reading_grade_fk=2.0)
    row.update(kw)
    return row


def term_rows():
    rows=[]
    for term, baseline, cued_a, cued_b in [('t1','A','A','B'),('t2','multiple','A','B'),('t3','A','B','A')]:
        rows.append(response(term+'0',term,label=baseline))
        for condition in ['C1','C2']:
            rows.extend([response(term+condition+'A',term,condition,'A',cued_a),
                         response(term+condition+'B',term,condition,'B',cued_b)])
    return rows


def control(rid,item,author,condition,correct,n_words,**kw):
    row=response(rid,item,condition,'NA',None,set='expertqa',term_id=None,
                 author_id=author,expected_cue_fields={'C1':['CS'],'C2':['CS'],'C3':['CS']},context_index=None,field_a=None,field_b=None,
                 cue_field=None if condition=='C0' else 'CS',correct=correct,n_words=n_words,
                 names_field=False,stereotyped=False,change_vs_c0='none')
    row.update(kw)
    return row


class InputTests(unittest.TestCase):
    def test_duplicate_response_and_design_cells_are_rejected(self):
        rows=term_rows()
        with self.assertRaisesRegex(ValueError,'Duplicate response_id'):
            fio.normalize_records(rows+[rows[0]])
        duplicate={**rows[0],'response_id':'other','request_id':'other'}
        with self.assertRaisesRegex(ValueError,'Duplicate design cell'):
            fio.normalize_records(rows+[duplicate])

    def test_missing_phase_and_author_are_not_imputed(self):
        row=response('a'); del row['response_phase']
        with self.assertRaisesRegex(ValueError,'response_phase'):
            fio.normalize_records([row])
        row=control('e','q','author','C0',1,10); row.pop('author_id')
        with self.assertRaisesRegex(ValueError,'author_id'):
            fio.normalize_records([row])

    def test_csv_zero_false_and_missing_remain_distinct(self):
        row=control('e','q','author','C0',0,0,names_field=False,reading_grade_fk=None)
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'records.csv'
            with p.open('w',newline='') as f:
                w=csv.DictWriter(f,fieldnames=list(row));w.writeheader();w.writerow({k:json.dumps(v) if isinstance(v,dict) else v for k,v in row.items()})
            got=fio.load_records(p)[0]
        self.assertEqual(got['correct'],0)
        self.assertIs(got['names_field'],False)
        self.assertIsNone(got['reading_grade_fk'])

    def test_order_join_remaps_displayed_labels_and_mentions(self):
        row=response('r',judge_ab=None,judge_ba=None)
        judges=[dict(response_id='r',order='AB',choice='A',mentioned_A=True,mentioned_B=False),
                dict(response_id='r',order='BA',choice='B',mentioned_A=False,mentioned_B=True)]
        got=fio.join_judgments([row],judges)[0]
        self.assertEqual((got['judge_ab'],got['judge_ba']),('A','A'))
        self.assertTrue(got['mentioned_a_ba'])
        self.assertFalse(got['mentioned_b_ba'])
        with self.assertRaisesRegex(ValueError,'Unknown response'):
            fio.join_judgments([row],[{**judges[0],'response_id':'unknown'}])

    def test_missing_judge_stays_missing_and_invalid_flag_fails(self):
        rows=fio.normalize_records([response('r',judge_ab=None,judge_ba=None)])
        self.assertIsNone(rows[0]['judge_ab'])
        with self.assertRaisesRegex(ValueError,'mentioned_a_ab'):
            fio.normalize_records([response('r',mentioned_a_ab='maybe')])


    def test_same_term_context_cannot_use_another_question_id(self):
        rows=term_rows()
        rows[1]['item_id']='different-item'
        rows[1]['question']='A changed question with a different meaning?'
        with self.assertRaisesRegex(ValueError,'term/context question'):
            fio.normalize_records(rows)

    def test_expert_reference_is_frozen_across_conditions(self):
        rows=[control('a0','a','x','C0',1,10,reference_answer='Answer one.'),
              control('a1','a','x','C1',1,10,reference_answer='Another answer.')]
        with self.assertRaisesRegex(ValueError,'item identity'):
            fio.normalize_records(rows)
        rows[1]['reference_answer']=None
        with self.assertRaisesRegex(ValueError,'item identity'):
            fio.normalize_records(rows)

    def test_term_references_must_match_across_contexts(self):
        first=response('a')
        second=response('b',context_index=1,item_id='t1:1',reference_a='Different meaning.')
        with self.assertRaisesRegex(ValueError,'term references'):
            fio.normalize_records([first,second])


class AnalysisTests(unittest.TestCase):
    def test_equal_term_weight_and_matched_paired_test(self):
        got=fr.analyze(fio.normalize_records(term_rows()),expected_contexts=1,bootstrap=100,seed=7)
        row=next(r for r in got['rq1_comparisons'] if r['condition']=='C1' and r['metric']=='chose_target')
        self.assertEqual(row['n_terms'],3)
        self.assertAlmostEqual(row['baseline_mean'],1/3)
        self.assertAlmostEqual(row['condition_mean'],2/3)
        self.assertAlmostEqual(row['mean_difference'],1/3)
        self.assertAlmostEqual(row['t_statistic'],0.7559289460184545)
        self.assertAlmostEqual(row['p_value_two_sided'],0.5285954792089683)

    def test_missing_context_excludes_term_instead_of_filling_zero(self):
        rows=term_rows(); rows=[r for r in rows if r['response_id']!='t2C1B']
        got=fr.analyze(fio.normalize_records(rows),expected_contexts=1,bootstrap=100)
        row=next(r for r in got['rq1_comparisons'] if r['condition']=='C1' and r['metric']=='chose_target')
        self.assertEqual(row['n_terms'],2)
        self.assertIn('t2',row['excluded_terms'])
        self.assertTrue(got['coverage']['incomplete_rq1_groups'])

    def test_order_conflict_is_unresolved_and_not_a_zero(self):
        rows=term_rows(); rows[1]['judge_ba']='B'
        got=fr.analyze(fio.normalize_records(rows),expected_contexts=1,bootstrap=100)
        self.assertEqual(got['coverage']['order_conflicts'],['t1C1A'])
        row=next(r for r in got['rq1_comparisons'] if r['condition']=='C1' and r['metric']=='chose_target')
        self.assertEqual(row['n_terms'],2)

    def test_followups_repeats_pilot_and_roommate_are_separate(self):
        rows=term_rows()
        rows.extend([response('fu',response_phase='followup'),response('repeat',repeat_index=1,temperature=.7),
                     response('pilot',split='pilot'),response('roommate',condition='C2-R',target='A')])
        got=fr.analyze(fio.normalize_records(rows),expected_contexts=1,bootstrap=100)
        self.assertEqual(got['coverage']['main_responses'],15)
        self.assertEqual(got['coverage']['excluded_counts'],dict(followup=1,repeat=1,pilot=1,roommate=1))

    def test_author_bootstrap_uses_whole_unequal_clusters(self):
        rows=[]
        for i,(author,diff) in enumerate([('a',1),('a',1),('a',1),('b',0)]):
            rows += [control(f'{i}0',str(i),author,'C0',0,10),control(f'{i}1',str(i),author,'C1',diff,12)]
        got=fr.analyze(fio.normalize_records(rows),bootstrap=200,seed=42)
        row=next(r for r in got['rq2'] if r['metric']=='correct' and r['condition']=='C1')
        self.assertAlmostEqual(row['mean_difference'],.75)
        self.assertEqual(row['n_authors'],2)
        self.assertEqual(row['ci_low'],0)
        self.assertEqual(row['ci_high'],1)
        self.assertEqual(got,fr.analyze(fio.normalize_records(list(reversed(rows))),bootstrap=200,seed=42))

    def test_missing_declared_cue_is_not_silently_averaged(self):
        design={'C1':['CS','STAT'],'C2':['CS'],'C3':['HLTH']}
        rows=[control('a0','a','x','C0',0,10,expected_cue_fields=design),
              control('a1','a','x','C1',1,20,expected_cue_fields=design)]
        got=fr.analyze(fio.normalize_records(rows),bootstrap=100)
        c=next(r for r in got['rq2'] if r['metric']=='correct' and r['condition']=='C1')
        self.assertEqual(c['n_items'],0)
        self.assertIsNone(c['mean_difference'])
        missing=next(r for r in got['coverage']['missing_rq2_pairs'] if r['condition']=='C1')
        self.assertEqual(missing['missing_cue_fields'],['STAT'])
        self.assertTrue(any(r['condition']=='C2' for r in got['coverage']['missing_rq2_pairs']))
        self.assertTrue(any(r['condition']=='C3' for r in got['coverage']['missing_rq2_pairs']))

    def test_c0_only_controls_report_all_missing_conditions(self):
        rows=[control('a0','a','x','C0',1,10)]
        got=fr.analyze(fio.normalize_records(rows),bootstrap=100)
        self.assertEqual({r['condition'] for r in got['coverage']['missing_rq2_pairs']},{'C1','C2','C3'})
        self.assertTrue(all(r['n_items']==0 for r in got['rq2']))

    def test_control_design_cannot_be_inferred_from_observed_cues(self):
        row=control('a0','a','x','C0',1,10);row.pop('expected_cue_fields')
        with self.assertRaisesRegex(ValueError,'expected_cue_fields'):
            fio.normalize_records([row])

    def test_missing_correctness_does_not_become_zero(self):
        rows=[control('a0','a','x','C0',1,10),control('a1','a','x','C1',None,20)]
        got=fr.analyze(fio.normalize_records(rows),bootstrap=100)
        c=next(r for r in got['rq2'] if r['metric']=='correct' and r['condition']=='C1')
        length=next(r for r in got['rq2'] if r['metric']=='n_words' and r['condition']=='C1')
        self.assertEqual(c['n_items'],0)
        self.assertIsNone(c['mean_difference'])
        self.assertEqual(length['mean_difference'],10)

    def test_no_answer_without_question_flag_does_not_become_asks(self):
        rows=[response('a','a',label='no_answer'),response('b','b',label='no_answer',asked_clarification=True)]
        got=fr.analyze(fio.normalize_records(rows),expected_contexts=1,bootstrap=100)
        rates={r['behavior']:r['rate'] for r in got['rq3']}
        self.assertEqual(rates['asks'],.5)
        self.assertEqual(rates['asks_or_no_answer'],.5)
        self.assertEqual(rates['no_answer'],0)

    def test_unexpected_context_is_rejected(self):
        rows=fio.normalize_records([response('a',context_index=6)])
        with self.assertRaisesRegex(ValueError,'context_index'):
            fr.analyze(rows,expected_contexts=6,bootstrap=100)

    def test_cli_will_not_overwrite_a_colliding_input_csv(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'rq1_rates.csv'
            rows=term_rows()
            with path.open('w',newline='') as f:
                writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
            before=path.read_bytes()
            with self.assertRaisesRegex(ValueError,'overwrite'):
                fr.main(['--input',str(path),'--output-dir',d,'--expected-contexts','1','--bootstrap','100'])
            self.assertEqual(path.read_bytes(),before)

    def test_cli_writes_tables_without_changing_input(self):
        with tempfile.TemporaryDirectory() as d:
            inp=Path(d)/'input.jsonl'; out=Path(d)/'out'
            inp.write_text(''.join(json.dumps(r)+'\n' for r in term_rows()))
            before=inp.read_bytes()
            self.assertEqual(fr.main(['--input',str(inp),'--output-dir',str(out),'--expected-contexts','1','--bootstrap','100']),0)
            self.assertEqual(inp.read_bytes(),before)
            self.assertTrue((out/'rq1_comparisons.csv').exists())
            self.assertTrue((out/'analysis_metadata.json').exists())


class SamplingTests(unittest.TestCase):
    def test_blind_exports_and_grouped_frozen_split(self):
        rows=term_rows()
        sample=vs.make_sample(fio.normalize_records(rows),n=12,seed=42)
        self.assertEqual(len(sample['blind_rows']),12)
        allowed={'sample_id','question','reference_a','reference_b','reference_answer','response_text','human_meaning','human_correct','human_mentioned_a','human_mentioned_b','human_asked_clarification','human_notes'}
        for row in sample['blind_rows']:
            self.assertEqual(set(row),allowed)
            self.assertEqual(row['human_meaning'],'')
            self.assertTrue(all(row[f]=='' for f in ['human_correct','human_mentioned_a','human_mentioned_b','human_asked_clarification','human_notes']))
            self.assertNotIn('model',row)
        assignments={}
        for row in sample['private_key']:
            group=row['group_id']
            self.assertTrue(group not in assignments or assignments[group]==row['split'])
            assignments[group]=row['split']
        self.assertEqual(set(assignments.values()),{'development','held_out'})
        self.assertEqual(sample,vs.make_sample(fio.normalize_records(list(reversed(rows))),n=12,seed=42))

    def test_control_coding_packet_requires_reference_answers(self):
        rows=[control('a','a','x','C0',1,10),control('b','b','y','C0',1,10)]
        with self.assertRaisesRegex(ValueError,'reference_answer'):
            vs.make_sample(fio.normalize_records(rows),n=2)

    def test_sampling_too_few_responses_or_groups_fails(self):
        with self.assertRaisesRegex(ValueError,'available'):
            vs.make_sample(fio.normalize_records(term_rows()),n=200)
        with self.assertRaisesRegex(ValueError,'groups'):
            vs.make_sample(fio.normalize_records(term_rows()[:5]),n=5)

    def test_development_manifest_excludes_entire_term_and_author_groups(self):
        rows=term_rows()+[control('c1','q1','used-author','C0',1,10,reference_answer='A reference.'),
                          control('c2','q2','fresh-author','C0',1,10,reference_answer='Another reference.')]
        sample=vs.make_sample(fio.normalize_records(rows),n=7,exclude_manifest={'term_ids':['t1'],'author_ids':['used-author'],'response_ids':['t2C1A']})
        chosen=sample['private_key']
        self.assertFalse(any(r['group_id'] in {'term:t1','author:used-author'} or r['response_id']=='t2C1A' for r in chosen))
        self.assertEqual(sample['metadata']['development_manifest_excluded'],7)

    def test_development_manifest_is_validated_not_silently_ignored(self):
        for manifest in [{'term_id':['t1']},{'term_ids':'t1'},{'term_ids':['t1','t1']},{'response_ids':['']}]:
            with self.subTest(manifest=manifest),self.assertRaises(ValueError):
                vs.make_sample(fio.normalize_records(term_rows()),n=5,exclude_manifest=manifest)

    def test_semantic_term_exclusion_survives_renamed_ids(self):
        rows=term_rows()
        for row in rows:row['term']=' Replication ' if row['term_id']=='t1' else row['term_id']
        sample=vs.make_sample(fio.normalize_records(rows),n=7,exclude_manifest={'schema_version':1,'term_names':['replication']})
        self.assertTrue(all(row['group_id']!='term:t1' for row in sample['private_key']))
        self.assertEqual(sample['metadata']['development_manifest_excluded'],5)
        with self.assertRaisesRegex(ValueError,'term text'):
            vs.make_sample(fio.normalize_records(term_rows()),n=7,exclude_manifest={'term_names':['replication']})

    def test_padded_ids_cannot_bypass_development_exclusions(self):
        for field in ['response_ids','term_ids','author_ids']:
            with self.subTest(manifest_field=field),self.assertRaisesRegex(ValueError,'unpadded'):
                vs.make_sample(fio.normalize_records(term_rows()),n=5,exclude_manifest={field:[' used ']})
        for field in ['response_id','term_id','author_id']:
            rows=term_rows()
            if field=='response_id':rows[0][field]=' '+rows[0][field]+' '
            elif field=='term_id':
                for row in rows:row[field]=' '+row[field]+' '
            else:rows += [control('extra','q',' used-author ','C0',1,10,reference_answer='A reference.')]
            with self.subTest(record_field=field),self.assertRaisesRegex(ValueError,'unpadded'):
                vs.make_sample(fio.normalize_records(rows),n=5,exclude_manifest={'author_ids':['used-author']})

    def test_exclusion_schema_version_is_exact(self):
        for value in [True,False,2,'1',None]:
            with self.subTest(version=value),self.assertRaisesRegex(ValueError,'schema version'):
                vs.make_sample(fio.normalize_records(term_rows()),n=5,exclude_manifest={'schema_version':value})

    def test_semantic_term_names_and_ids_must_have_one_to_one_binding(self):
        for action in ['one_name_two_ids','one_id_two_names']:
            rows=term_rows()
            for row in rows:row['term']='replication' if row['term_id']=='t1' else row['term_id']
            if action=='one_name_two_ids':
                for row in rows:
                    if row['term_id']=='t2':row['term']=' Replication '
            else:rows[1]['term']='Different word'
            with self.subTest(action=action),self.assertRaisesRegex(ValueError,'one-to-one'):
                vs.make_sample(fio.normalize_records(rows),n=7)


if __name__=='__main__': unittest.main()
