import copy
import hashlib
import io
import json
import unittest
import xml.etree.ElementTree as ET
import zipfile

import openpyxl

from tokenify_analysis.run_package_audit import audit_records, expected_schema, verify_score_workbook


SYSTEM = 'Return rationale first, mentioned second, and choice last.'


def fixture():
    records = {
        'S01': dict(term='sample', context='No cue', references={'A': 'computer science: A small test object.', 'B': 'statistics: A subset of observations.'}, answer='A small test object.'),
        'S02': dict(term='sample', context='Statistics', references={'A': 'computer science: A small test object.', 'B': 'statistics: A subset of observations.'}, answer='Both meanings are given.'),
    }
    mapping = [dict(row=2,id='S01',target='A'),dict(row=3,id='S01',target='B'),dict(row=4,id='S02',target='A')]
    authority = dict(records=records,mapping=mapping,humans={2:1,3:0,4:0},judgment_ids={2:'J01',3:'J02',4:'J03'},conditions={2:'C0',3:'C0',4:'C1'},adjudications={2:None,3:None,4:None},sha256='a'*64)
    config=dict(model='test-model',system=SYSTEM,options=dict(temperature=0,seed=42,num_ctx=8192,num_predict=200),think=False,input_sha256='b'*64,workers=1)
    config['fingerprint']=hashlib.sha256(json.dumps(config,sort_keys=True).encode()).hexdigest()
    data=dict(judgments=[],mapping=copy.deepcopy(mapping),config=config)
    for rid,rec in records.items():
        labels={'A':'computer_science','B':'statistics'}
        for order in ['AB','BA']:
            prompt=dict(term=rec['term'],student_context=rec['context'],references=[dict(id=labels[s],definition=rec['references'][s]) for s in order],answer=rec['answer'])
            result=dict(rationale='Evidence is stated.',mentioned=dict(computer_science=True,statistics=False),choice='computer_science' if rid=='S01' else 'multiple')
            raw=dict(model='test-model',done=True,done_reason='stop',message=dict(role='assistant',content=json.dumps(result)))
            attempt=dict(messages=[dict(role='system',content=SYSTEM),dict(role='user',content=json.dumps(prompt))],raw=raw)
            data['judgments'].append(dict(id=rid,order=order,status='ok',attempts=[attempt],labels=labels,prompt=prompt,schema=expected_schema(labels),result=result))
    score_rows=[dict(**m,human=str(authority['humans'][m['row']]),AB=int(m['id']=='S01' and m['target']=='A'),BA=int(m['id']=='S01' and m['target']=='A'),term='sample',condition=authority['conditions'][m['row']]) for m in mapping]
    return data,authority,score_rows


def workbook_fixture():
    _,authority,scores=fixture()
    wb=openpyxl.Workbook();wb.remove(wb.active)
    review=wb.create_sheet('Review');agreement=wb.create_sheet('Agreement');check=wb.create_sheet('Qwen_check')
    review.append(['#','Term','context','A','B','answer','human label'])
    for rid,rec in authority['records'].items():
        review.append([rid,rec['term'],rec['context'],rec['references']['A'],rec['references']['B'],rec['answer'],'Meaning A' if rid=='S01' else "Lists several, doesn't pick"])
    agreement.append(['judgment','row','term','condition','target','Xinyi','KC','agree'])
    check.append(['judgment','id','term','condition','target','r1','r2','James','human','AB','BA','final','agree','oldAB','oldBA'])
    for index,item in enumerate(scores):
        n=index+2;review_row=2 if item['id']=='S01' else 3
        agreement.append([f'J{index+1:02}',f'=Review!A{review_row}',None,item['condition'],item['target'],int(item['human']),None,None])
        check.append([f'J{index+1:02}',item['id'],'sample',item['condition'],item['target'],None,None,None,int(item['human']),float(item['AB']),float(item['BA'])])
    stream=io.BytesIO();wb.save(stream);wb.close()
    return stream.getvalue()


class RunPackageAuditTests(unittest.TestCase):
    def run_audit(self,data=None,authority=None,scores=None,**kwargs):
        d,a,s=fixture()
        actual_data=data or d;actual_authority=authority or a
        if 'run_status' not in kwargs:
            kwargs['run_status']=dict(status='completed',model=actual_data['config'].get('model'),
                                     input_sha256=actual_data['config'].get('input_sha256'),
                                     source_workbook_sha256=actual_authority['sha256'],script_sha256='c'*64,
                                     jobs=len(actual_data['judgments']),valid_judgments=len(actual_data['judgments']),
                                     validation_passed=all(v is not None for v in actual_authority['humans'].values()))
        return audit_records(data or d,authority or a,score_rows=s if scores is None else scores,system_prompt=SYSTEM,**kwargs)

    def test_field_identity_does_not_follow_display_position(self):
        report,rows,judgments=self.run_audit()
        self.assertEqual(report['target_order_consistent'],3)
        self.assertEqual(report['all_rows_kappa_ab'],1)
        self.assertEqual(report['all_rows_kappa_ba'],1)
        self.assertEqual(judgments[1]['normalized_choice'],'A')
        self.assertEqual(len(rows),3)

    def test_swapped_presentation_must_keep_exact_reference_identities(self):
        data,_,_=fixture()
        data['judgments'][1]['prompt']['references'].reverse()
        with self.assertRaisesRegex(ValueError,'prompt'):
            self.run_audit(data=data)

    def test_changed_answer_cannot_reuse_old_judgment(self):
        data,_,_=fixture();data['judgments'][0]['prompt']['answer']='Different answer.'
        with self.assertRaisesRegex(ValueError,'prompt'):
            self.run_audit(data=data)

    def test_missing_duplicate_and_unknown_orders_are_rejected(self):
        for action in ['missing','duplicate','unknown']:
            data,_,_=fixture()
            if action=='missing':data['judgments'].pop()
            elif action=='duplicate':data['judgments'].append(copy.deepcopy(data['judgments'][0]))
            else:data['judgments'][0]['order']='AA'
            with self.subTest(action=action),self.assertRaises(ValueError):self.run_audit(data=data)

    def test_unknown_fields_and_false_normalized_result_are_rejected(self):
        for action in ['choice','result','mentioned','schema']:
            data,_,_=fixture();entry=data['judgments'][0]
            if action=='choice':entry['attempts'][0]['raw']['message']['content']='{"rationale":"Evidence","mentioned":{"computer_science":true,"statistics":false},"choice":"medicine_healthcare"}'
            elif action=='result':entry['result']['choice']='other'
            elif action=='mentioned':entry['attempts'][0]['raw']['message']['content']='{"rationale":"Evidence","mentioned":{"computer_science":1,"statistics":false},"choice":"computer_science"}'
            else:entry['schema']['properties']['choice']['enum'].reverse()
            with self.subTest(action=action),self.assertRaises(ValueError):self.run_audit(data=data)

    def test_duplicate_json_keys_are_not_valid_literal_json(self):
        data,_,_=fixture();data['judgments'][0]['attempts'][0]['raw']['message']['content']='{"rationale":"Evidence","rationale":"Hidden replacement","mentioned":{"computer_science":true,"statistics":false},"choice":"computer_science"}'
        with self.assertRaisesRegex(ValueError,'Duplicate JSON key'):self.run_audit(data=data)

    def test_key_order_and_rationale_word_limit_are_enforced(self):
        for action in ['order','words','truncated']:
            data,_,_=fixture();entry=data['judgments'][0]
            if action=='truncated':entry['attempts'][0]['raw']['done_reason']='length'
            else:
                obj=entry['result']
                if action=='order':obj=dict(choice=obj['choice'],mentioned=obj['mentioned'],rationale=obj['rationale'])
                else:obj['rationale']='word '*41
                entry['attempts'][0]['raw']['message']['content']=json.dumps(obj);entry['result']=obj
            with self.subTest(action=action),self.assertRaises(ValueError):self.run_audit(data=data)

    def test_coherence_is_enforced_but_primary_definition_can_mention_both(self):
        data,_,_=fixture()
        for entry in data['judgments']:
            entry['result']['mentioned']['statistics']=True
            entry['attempts'][0]['raw']['message']['content']=json.dumps(entry['result'])
        self.run_audit(data=data)
        data['judgments'][0]['result']['mentioned']['computer_science']=False
        data['judgments'][0]['attempts'][0]['raw']['message']['content']=json.dumps(data['judgments'][0]['result'])
        with self.assertRaisesRegex(ValueError,'not mentioned'):self.run_audit(data=data)

    def test_package_scores_and_human_labels_cannot_override_authority(self):
        for action in ['score','human','row','identity']:
            _,_,scores=fixture()
            if action=='score':scores[0]['AB']=0
            elif action=='human':scores[0]['human']='0'
            elif action=='row':scores[1]['row']=2
            else:scores[0]['id']='S99'
            with self.subTest(action=action),self.assertRaises(ValueError):self.run_audit(scores=scores)

    def test_claimed_statistics_are_checked_not_trusted(self):
        report,_,_=self.run_audit(summary={'judgments':999,'passes':False})
        self.assertFalse(report['record_integrity_pass'])
        self.assertEqual(report['pilot_gate_status'],'package_claim_mismatch')
        self.assertTrue(report['package_claim_discrepancies'])

    def test_configuration_fingerprint_and_messages_are_bound_to_prompt(self):
        for action in ['fingerprint','system','user']:
            data,_,_=fixture()
            if action=='fingerprint':data['config']['fingerprint']='f'*64
            elif action=='system':data['judgments'][0]['attempts'][0]['messages'][0]['content']='Changed system'
            else:data['judgments'][0]['attempts'][0]['messages'][1]['content']='{}'
            with self.subTest(action=action),self.assertRaises(ValueError):self.run_audit(data=data)

    def test_missing_human_label_is_never_zero_imputed(self):
        data,authority,scores=fixture();authority['humans'][2]=None;scores[0]['human']=None
        report,_,_=self.run_audit(data=data,authority=authority,scores=scores)
        self.assertEqual(report['human_labels_available'],2)
        self.assertIsNone(report['all_rows_kappa_ab'])
        self.assertEqual(report['pilot_gate_status'],'pending_human_labels')

    def test_separate_score_workbook_accepts_numeric_excel_scores(self):
        data,authority,_=fixture();_,rows,_=self.run_audit(data=data,authority=authority)
        verify_score_workbook(workbook_fixture(),authority,rows,'current workbook')

    def test_separate_score_workbook_rejects_score_and_human_changes(self):
        _,authority,_=fixture();_,rows,_=self.run_audit()
        for sheet,cell,value in [('Qwen_check','J2',0),('Qwen_check','H2',1),('Review','F2','Changed answer')]:
            wb=openpyxl.load_workbook(io.BytesIO(workbook_fixture()));wb[sheet][cell]=value
            stream=io.BytesIO();wb.save(stream);wb.close()
            with self.subTest(sheet=sheet,cell=cell),self.assertRaises(ValueError):
                verify_score_workbook(stream.getvalue(),authority,rows,'current workbook')

    def test_full_choice_flip_is_reported_even_when_target_scores_match(self):
        data,_,scores=fixture();entry=data['judgments'][-1]
        entry['result']['choice']='other';entry['attempts'][0]['raw']['message']['content']=json.dumps(entry['result'])
        report,_,_=self.run_audit(data=data,scores=scores)
        self.assertEqual(report['target_order_consistent'],3)
        self.assertEqual(report['choice_order_disagreement_ids'],['S02'])
        self.assertEqual(report['choice_order_agreement'],.5)

    def test_missing_configuration_cannot_be_hidden_by_a_new_fingerprint(self):
        for fields in [('options',),('input_sha256',),('think','workers','options','input_sha256')]:
            data,_,_=fixture()
            for field in fields:data['config'].pop(field)
            core={k:v for k,v in data['config'].items() if k not in {'fingerprint','runtime','model_details'}}
            data['config']['fingerprint']=hashlib.sha256(json.dumps(core,sort_keys=True).encode()).hexdigest()
            with self.subTest(fields=fields),self.assertRaises(ValueError):self.run_audit(data=data)

    def test_invalid_configuration_types_and_hashes_are_rejected(self):
        for field,value in [('model',''),('workers',True),('think',0),('input_sha256','not-a-hash'),('options',{})]:
            data,_,_=fixture();data['config'][field]=value
            core={k:v for k,v in data['config'].items() if k not in {'fingerprint','runtime','model_details'}}
            data['config']['fingerprint']=hashlib.sha256(json.dumps(core,sort_keys=True).encode()).hexdigest()
            with self.subTest(field=field),self.assertRaises(ValueError):self.run_audit(data=data)

    def test_required_run_provenance_cannot_be_omitted(self):
        statuses=[None,{},dict(status='still_running',model='other-model')]
        base=dict(status='completed',model='test-model',input_sha256='b'*64,
                  source_workbook_sha256='a'*64,script_sha256='c'*64,jobs=4,valid_judgments=4,validation_passed=True)
        for field,value in [('status','still_running'),('model','other-model'),('source_workbook_sha256',None),('script_sha256','bad-hash')]:
            status=copy.deepcopy(base);status[field]=value;statuses.append(status)
        for status in statuses:
            with self.subTest(status=status),self.assertRaises(ValueError):self.run_audit(run_status=status)

    def test_mismatched_source_hash_blocks_the_gate(self):
        data,authority,_=fixture()
        status=dict(status='completed',model=data['config']['model'],input_sha256=data['config']['input_sha256'],
                    source_workbook_sha256='d'*64,script_sha256='c'*64,jobs=4,valid_judgments=4,validation_passed=True)
        report,_,_=self.run_audit(run_status=status)
        self.assertFalse(report['record_integrity_pass'])
        self.assertFalse(report['numerical_gate_met'])

    def test_cached_formula_scores_are_not_literal_machine_scores(self):
        _,authority,_=fixture();_,rows,_=self.run_audit();blob=io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(workbook_fixture())) as old,zipfile.ZipFile(blob,'w') as new:
            for entry in old.infolist():
                content=old.read(entry.filename)
                if entry.filename=='xl/worksheets/sheet3.xml':
                    root=ET.fromstring(content);ns='{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
                    cell=next(c for c in root.iter(ns+'c') if c.attrib['r']=='J2')
                    formula=ET.Element(ns+'f');formula.text='0';cell.insert(0,formula)
                    content=ET.tostring(root)
                new.writestr(entry,content)
        with self.assertRaisesRegex(ValueError,'literal'):
            verify_score_workbook(blob.getvalue(),authority,rows,'current workbook')

    def test_valid_outputs_cannot_be_retried(self):
        data,_,_=fixture();entry=data['judgments'][0]
        retry=copy.deepcopy(entry['attempts'][0]);retry['messages'].append(dict(role='user',content='Your previous output failed validation: invalid. Re-evaluate the supplied answer and return a complete valid JSON object. no_answer is permitted only when there is no definition at all.'))
        entry['attempts'].append(retry)
        with self.assertRaisesRegex(ValueError,'valid output'):self.run_audit(data=data)

    def test_invalid_first_output_can_be_retried_with_recorded_feedback(self):
        data,_,_=fixture();entry=data['judgments'][0]
        failed=copy.deepcopy(entry['attempts'][0]);failed['raw']['message']['content']='not JSON';failed['error']='invalid JSON'
        success=copy.deepcopy(entry['attempts'][0]);success['messages'].append(dict(role='user',content='Your previous output failed validation: invalid JSON. Re-evaluate the supplied answer and return a complete valid JSON object. no_answer is permitted only when there is no definition at all.'))
        entry['attempts']=[failed,success]
        report,_,_=self.run_audit(data=data)
        self.assertEqual(report['actual_attempts'],5)
        self.assertEqual(report['first_attempt_valid'],3)

    def test_source_hash_can_be_recorded_in_the_summary(self):
        data,authority,_=fixture()
        status=dict(status='completed',model=data['config']['model'],input_sha256=data['config']['input_sha256'],
                    script_sha256='c'*64,jobs=4,valid_judgments=4,validation_passed=True)
        report,_,_=self.run_audit(run_status=status,summary={'source_workbook_sha256':authority['sha256']})
        self.assertTrue(report['record_integrity_pass'])
        self.assertTrue(report['numerical_gate_met'])

    def test_more_than_three_saved_attempts_is_rejected(self):
        data,_,_=fixture();entry=data['judgments'][0];original=copy.deepcopy(entry['attempts'][0]);entry['attempts']=[]
        for index in range(4):
            attempt=copy.deepcopy(original)
            if index:attempt['messages'].append(dict(role='user',content='Your previous output failed validation: invalid JSON. Re-evaluate the supplied answer and return a complete valid JSON object. no_answer is permitted only when there is no definition at all.'))
            if index<3:attempt['raw']['message']['content']='not JSON';attempt['error']='invalid JSON'
            entry['attempts'].append(attempt)
        with self.assertRaisesRegex(ValueError,'attempt'):
            self.run_audit(data=data)


if __name__=='__main__':unittest.main()
