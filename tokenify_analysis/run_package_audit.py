"""Audit field-identifier judge packages against the unchanged pilot workbook."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
from pathlib import Path
import re
import zipfile

import openpyxl

from .judge_audit import agreement, workbook_humans

FIELDS = {'computer science':'computer_science', 'medicine/healthcare':'medicine_healthcare',
          'education/psychology':'education_psychology', 'statistics':'statistics'}
NON_REFERENCE = {'multiple','other','no_answer'}
PACKAGE_FILES = ('results.json','score_rows.json','summary.json','run_status.json',
                 'judge_prompt.txt','judge.py')
OPTIONAL_FILES = ('Tokenify_blind_review_Qwen_run3.xlsx',)
RETRY_SUFFIX = '. Re-evaluate the supplied answer and return a complete valid JSON object. no_answer is permitted only when there is no definition at all.'


def _unique(pairs):
    result={}
    for key,value in pairs:
        if key in result: raise ValueError(f'Duplicate JSON key: {key}')
        result[key]=value
    return result


def literal_json(text):
    return json.loads(text,object_pairs_hook=_unique,parse_constant=lambda x: (_ for _ in ()).throw(ValueError(f'Invalid JSON constant: {x}')))


def binary(value, *, allow_text=False, optional=False):
    if optional and value in (None,''): return None
    if allow_text and type(value) is str and value in {'0','1'}: return int(value)
    if type(value) in (int,float) and math.isfinite(value) and value in (0,1): return int(value)
    raise ValueError(f'Invalid binary value: {value!r}')


def _sha256(value,field):
    if not isinstance(value,str) or re.fullmatch(r'[0-9a-f]{64}',value) is None:
        raise ValueError(f'{field} requires a lowercase SHA-256 digest')


def _validate_config(config):
    required={'model','system','options','think','workers','input_sha256','fingerprint'}
    if not isinstance(config,dict) or not required.issubset(config):raise ValueError('Missing required run configuration fields')
    for field in ['model','system']:
        if not isinstance(config[field],str) or not config[field].strip():raise ValueError(f'Invalid configuration {field}')
    if type(config['think']) is not bool or type(config['workers']) is not int or config['workers']<1:
        raise ValueError('think must be boolean and workers a positive integer')
    options=config['options']
    if not isinstance(options,dict) or not {'temperature','seed','num_ctx','num_predict'}.issubset(options):
        raise ValueError('Missing required generation options')
    temperature=options['temperature']
    if type(temperature) not in (int,float) or not math.isfinite(temperature) or temperature<0:raise ValueError('Invalid temperature')
    if type(options['seed']) is not int or options['seed']<0:raise ValueError('Invalid seed')
    for field in ['num_ctx','num_predict']:
        if type(options[field]) is not int or options[field]<1:raise ValueError(f'Invalid {field}')
    _sha256(config['input_sha256'],'input_sha256');_sha256(config['fingerprint'],'fingerprint')
    if 'runtime' in config and (not isinstance(config['runtime'],dict) or not isinstance(config['runtime'].get('version'),str) or not config['runtime']['version'].strip()):
        raise ValueError('Invalid runtime metadata')
    if 'model_details' in config and not isinstance(config['model_details'],dict):raise ValueError('Invalid model details')


def _validate_run_status(status,config,summary):
    required={'status','model','input_sha256','script_sha256','jobs','valid_judgments','validation_passed'}
    if not isinstance(status,dict) or not required.issubset(status):raise ValueError('Missing required completed-run provenance')
    if status['status']!='completed' or status['model']!=config['model']:raise ValueError('Run status must be completed and match the configured model')
    for field in ['input_sha256','script_sha256']:_sha256(status[field],field)
    sources=[manifest for manifest in [status,summary] if isinstance(manifest,dict) and 'source_workbook_sha256' in manifest]
    if not sources:raise ValueError('Missing required frozen-workbook source hash')
    for manifest in sources:_sha256(manifest['source_workbook_sha256'],'source_workbook_sha256')
    for field in ['jobs','valid_judgments']:
        if type(status[field]) is not int or status[field]<0:raise ValueError(f'Invalid run status {field}')
    if status['jobs']<1 or type(status['validation_passed']) is not bool:raise ValueError('Invalid completed-run counts or validation status')


def expected_schema(labels):
    fields=sorted(labels.values())
    return {'type':'object','properties':{
        'rationale':{'type':'string','description':'Evidence and decision in at most 40 words.'},
        'mentioned':{'type':'object','properties':{field:{'type':'boolean'} for field in fields},'required':fields,'additionalProperties':False},
        'choice':{'type':'string','enum':fields+['multiple','other','no_answer']}},
        'required':['rationale','mentioned','choice'],'additionalProperties':False}


def labels_for(record):
    try: labels={sense:FIELDS[record['references'][sense].split(':',1)[0]] for sense in 'AB'}
    except (KeyError,AttributeError) as exc: raise ValueError('Unknown canonical reference field') from exc
    if len(set(labels.values()))!=2: raise ValueError('Reference fields must differ')
    return labels


def _validate_output(raw,labels,model):
    if raw.get('model')!=model or raw.get('done') is not True or raw.get('done_reason')!='stop':
        raise ValueError('Truncated, incomplete, or mismatched-model output')
    message=raw.get('message',{})
    if message.get('role')!='assistant' or not isinstance(message.get('content'),str): raise ValueError('Missing assistant output')
    obj=literal_json(message['content'])
    if not isinstance(obj,dict) or tuple(obj)!=('rationale','mentioned','choice'):
        raise ValueError('Output must contain rationale first, mentioned second, choice last')
    if not isinstance(obj['rationale'],str) or not 0<len(obj['rationale'].split())<=40:
        raise ValueError('Rationale must contain 1 to 40 words')
    if obj['choice'] not in set(labels.values())|NON_REFERENCE: raise ValueError('Unknown choice field')
    mentioned=obj['mentioned']
    if not isinstance(mentioned,dict) or set(mentioned)!=set(labels.values()) or any(type(v) is not bool for v in mentioned.values()):
        raise ValueError('Mention flags must be booleans for exactly the two reference fields')
    if obj['choice'] in mentioned and not mentioned[obj['choice']]: raise ValueError('Chosen reference is not mentioned')
    if obj['choice']=='no_answer' and any(mentioned.values()): raise ValueError('no_answer cannot mention a reference meaning')
    return obj


def read_authority(path, *, expected_answers=45, expected_rows=54):
    """Read raw Review text and reconstruct consensus from the original sources."""
    content=path if isinstance(path,bytes) else Path(path).read_bytes()
    wb=openpyxl.load_workbook(io.BytesIO(content),read_only=False,data_only=False)
    try:
        review=wb['Review']; records={}; row_ids={}
        for row in review:
            rid=row[0].value
            if not re.fullmatch(r'S\d+',str(rid or '')): continue
            if rid in records: raise ValueError(f'Duplicate Review ID: {rid}')
            values=[c.value for c in row[:6]]
            if any(not isinstance(x,str) or not x for x in values): raise ValueError(f'Incomplete Review record: {rid}')
            records[rid]=dict(term=values[1],context=values[2],references=dict(A=values[3],B=values[4]),answer=values[5])
            row_ids[row[0].row]=rid
        mapping=[]; judgments={}; conditions={}; adjudications={}; human_sources={}
        for row in wb['Agreement']:
            number=row[0].row; jid=row[0].value
            if not re.fullmatch(r'J\d+',str(jid or '')): continue
            match=re.fullmatch(r'=Review!A(\d+)',str(row[1].value or ''))
            if match is None or int(match[1]) not in row_ids: raise ValueError(f'Invalid Agreement join at row {number}')
            if jid in judgments.values(): raise ValueError('Duplicate Agreement judgment ID')
            mapping.append(dict(row=number,id=row_ids[int(match[1])],target=row[4].value))
            judgments[number]=jid; conditions[number]=row[3].value
            adjudications[number]=binary(wb['Qwen_check'].cell(number,8).value,optional=True)
            human_sources[number]=(review.cell(int(match[1]),7).value,row[5].value,adjudications[number])
        if len(records)!=expected_answers or len(mapping)!=expected_rows: raise ValueError('Authoritative workbook must contain all 45 answers and 54 target rows')
    finally: wb.close()
    humans,_=workbook_humans(io.BytesIO(content),mapping)
    return dict(records=records,mapping=mapping,humans=humans,judgment_ids=judgments,
                conditions=conditions,adjudications=adjudications,human_sources=human_sources,sha256=hashlib.sha256(content).hexdigest())


def _check_claim(discrepancies,path,actual,claimed):
    if isinstance(actual,float) and isinstance(claimed,(float,int)) and type(claimed) is not bool:
        same=math.isclose(actual,claimed,rel_tol=1e-10,abs_tol=1e-12)
    else: same=type(actual) is type(claimed) and actual==claimed
    if not same: discrepancies.append(dict(field=path,computed=actual,claimed=claimed))


def audit_records(data,authority, *, score_rows=None,summary=None,run_status=None,system_prompt=None):
    """Pure audit of raw judgments; package scores and summaries are comparison data."""
    canonical=authority['records']; mapping=authority['mapping']; humans=authority['humans']
    if data.get('mapping')!=mapping: raise ValueError('Package score mapping differs from the complete authoritative mapping')
    config=data['config'];_validate_config(config);_validate_run_status(run_status,config,summary)
    core={k:v for k,v in config.items() if k not in {'fingerprint','runtime','model_details'}}
    if hashlib.sha256(json.dumps(core,sort_keys=True).encode()).hexdigest()!=config.get('fingerprint'):
        raise ValueError('Run configuration fingerprint differs')
    if not isinstance(config.get('system'),str) or not config['system'] or system_prompt is not None and config['system']!=system_prompt:
        raise ValueError('Configured system prompt differs from packaged prompt')
    by_key={}; judgments=[]; attempts=0; first_valid=0
    for entry in data['judgments']:
        rid,order=entry['id'],entry['order']; key=(rid,order)
        if rid not in canonical or order not in {'AB','BA'} or key in by_key: raise ValueError(f'Unknown or duplicate judgment identity: {key}')
        if entry.get('status')!='ok' or not isinstance(entry.get('attempts'),list) or not 1<=len(entry['attempts'])<=3:
            raise ValueError(f'Incomplete judgment status or attempts: {key}')
        record=canonical[rid]; labels=labels_for(record)
        prompt=dict(term=record['term'],student_context=record['context'],references=[dict(id=labels[s],definition=record['references'][s]) for s in order],answer=record['answer'])
        if entry.get('labels')!=labels or entry.get('prompt')!=prompt: raise ValueError(f'Changed canonical labels or prompt identity: {key}')
        schema=expected_schema(labels)
        if entry.get('schema')!=schema or list(entry['schema']['properties']['mentioned']['properties'])!=sorted(labels.values()):
            raise ValueError(f'Changed or order-dependent JSON schema: {key}')
        for index,attempt in enumerate(entry['attempts']):
            messages=attempt.get('messages')
            if not isinstance(messages,list) or len(messages)!=(2 if index==0 else 3): raise ValueError(f'Unexpected attempt messages: {key}')
            if messages[0]!={'role':'system','content':config['system']} or messages[1].get('role')!='user' or literal_json(messages[1].get('content',''))!=prompt:
                raise ValueError(f'Attempt messages differ from the verified prompt: {key}')
            if index:
                previous=entry['attempts'][index-1]
                expected_feedback='Your previous output failed validation: '+previous.get('error','invalid')+RETRY_SUFFIX
                if messages[2]!={'role':'user','content':expected_feedback}:raise ValueError(f'Unexpected retry feedback: {key}')
            if index<len(entry['attempts'])-1:
                if 'raw' in attempt:
                    try:_validate_output(attempt['raw'],labels,config['model'])
                    except (ValueError,KeyError,TypeError):pass
                    else:raise ValueError(f'Retry after a valid output: {key}')
                if not isinstance(attempt.get('error'),str) or not attempt['error'].strip():raise ValueError(f'Prior failed attempt lacks validation error: {key}')
            elif attempt.get('error'):raise ValueError(f'Final successful attempt has a recorded error: {key}')
        obj=_validate_output(entry['attempts'][-1]['raw'],labels,config['model'])
        if obj!=entry.get('result'): raise ValueError(f'Stored result differs from literal model output: {key}')
        if len(entry['attempts'])==1:first_valid+=1
        attempts+=len(entry['attempts']); inverse={v:k for k,v in labels.items()}
        choice=inverse.get(obj['choice'],obj['choice'])
        mentioned={sense:obj['mentioned'][labels[sense]] for sense in 'AB'}
        by_key[key]=dict(choice=choice,mentioned=mentioned)
        judgments.append(dict(response_id=rid,order=order,normalized_choice=choice,field_choice=obj['choice'],mentioned_A=mentioned['A'],mentioned_B=mentioned['B'],rationale=obj['rationale'],rationale_words=len(obj['rationale'].split()),attempts=len(entry['attempts'])))
    if set(by_key)!={(rid,order) for rid in canonical for order in ['AB','BA']}: raise ValueError('Missing complete answer/reference-order coverage')
    rows=[]; seen_rows=set(); seen_targets=set()
    for item in mapping:
        row,rid,target=item['row'],item['id'],item['target']
        if row in seen_rows or (rid,target) in seen_targets or target not in {'A','B'}: raise ValueError('Duplicate or invalid mapping row/target')
        seen_rows.add(row);seen_targets.add((rid,target))
        rows.append(dict(**item,judgment=authority['judgment_ids'][row],term=canonical[rid]['term'],condition=authority['conditions'][row],
                         human=humans[row],AB=int(by_key[(rid,'AB')]['choice']==target),BA=int(by_key[(rid,'BA')]['choice']==target)))
    if score_rows is not None:
        claimed={}
        for item in score_rows:
            row=item['row']
            if row in claimed: raise ValueError('Duplicate declared score row')
            claimed[row]=item
        if set(claimed)!=seen_rows: raise ValueError('Declared score rows do not cover the complete authoritative mapping')
        for item in rows:
            claim=claimed[item['row']]
            for field in ['id','target','term','condition']:
                if claim.get(field)!=item[field]: raise ValueError(f'Declared score identity mismatch: {item["row"]}/{field}')
            for field in ['AB','BA']:
                if binary(claim.get(field))!=item[field]: raise ValueError(f'Declared score differs from literal judgments: {item["row"]}/{field}')
            if binary(claim.get('human'),allow_text=True,optional=True)!=item['human']:
                raise ValueError(f'Package human label differs from authoritative workbook: {item["row"]}')
    available=[r for r in rows if r['human'] is not None]
    consistent=[r for r in available if r['AB']==r['BA']]
    all_ab=agreement([r['human'] for r in available],[r['AB'] for r in available])
    all_ba=agreement([r['human'] for r in available],[r['BA'] for r in available])
    subset=agreement([r['human'] for r in consistent],[r['AB'] for r in consistent])
    n_matches=sum(r['AB']==r['BA'] for r in rows); all_humans=len(available)==len(rows)
    required=math.ceil(.9*len(rows)); order_pass=n_matches>=required
    kappa_pass=all_humans and all(x['kappa'] is not None and x['kappa']>=.6 for x in [all_ab,all_ba])
    numerical_pass=order_pass and kappa_pass
    discrepancies=[]
    if summary is not None:
        expected=dict(source_workbook_sha256=authority['sha256'],judgments=len(judgments),calls=attempts,valid=len(judgments),order_agreement_rows=n_matches,target_rows=len(rows),passes=numerical_pass)
        for key,value in expected.items():
            if key in summary:_check_claim(discrepancies,'summary.'+key,value,summary[key])
        for key,values in [('AB',all_ab),('BA',all_ba),('consensus',subset)]:
            for field,value in {**values,'matches':round(values['agreement']*values['n']) if values['n'] else 0}.items():
                if field in summary.get(key,{}):_check_claim(discrepancies,f'summary.{key}.{field}',value,summary[key][field])
    if run_status is not None:
        expected=dict(source_workbook_sha256=authority['sha256'],input_sha256=config['input_sha256'],jobs=len(judgments),valid_judgments=len(judgments),validation_passed=numerical_pass,consensus_kappa=subset['kappa'],consensus_kappa_rows=subset['n'],order_agreement=f'{n_matches}/{len(rows)}')
        for key,value in expected.items():
            if key in run_status:_check_claim(discrepancies,'run_status.'+key,value,run_status[key])
    status=('package_claim_mismatch' if discrepancies else 'pending_human_labels' if not all_humans else
            'fails_order_threshold' if not order_pass else 'fails_kappa_threshold' if not kappa_pass else
            'numeric_thresholds_met_semantic_review_required')
    choice_flips=sorted(rid for rid in canonical if by_key[(rid,'AB')]['choice']!=by_key[(rid,'BA')]['choice'])
    report=dict(run=(summary or {}).get('run','Field-identifier judge run'),model=config['model'],
                answers=len(canonical),logical_judgments=len(judgments),actual_attempts=attempts,valid_final_json=len(judgments),first_attempt_valid=first_valid,
                target_score_rows=len(rows),target_order_consistent=n_matches,target_order_agreement=n_matches/len(rows),required_order_matches=required,order_threshold_met=order_pass,
                human_labels_available=len(available),pending_human_rows=[r['row'] for r in rows if r['human'] is None],
                ab_vs_available_human=all_ab,ba_vs_available_human=all_ba,all_rows_kappa_ab=all_ab['kappa'] if all_humans else None,all_rows_kappa_ba=all_ba['kappa'] if all_humans else None,
                order_consistent_subset_vs_human=subset,conservative_all_row_kappa_threshold_met=kappa_pass,
                numerical_thresholds_met=numerical_pass,numerical_gate_met=numerical_pass and not discrepancies,
                target_order_disagreement_rows=[r['row'] for r in rows if r['AB']!=r['BA']],
                target_order_disagreement_ids=sorted({r['id'] for r in rows if r['AB']!=r['BA']}),
                choice_order_disagreement_ids=choice_flips,choice_order_agreement=(len(canonical)-len(choice_flips))/len(canonical),
                record_integrity_pass=not discrepancies,package_claim_discrepancies=discrepancies,pilot_gate_status=status,
                structural_coherence_flags=[],
                policy='At least 90% of all target rows must agree across reference orders. This conservative check requires both all-row kappas >=0.6. Filtered-subset kappa is descriptive only. Numerical thresholds do not establish semantic validity or settle the policy for conflicting orders.',
                source_identity='Every saved call prompt, answer, context and reference is matched to the authoritative workbook; all human scores come from its original sources and adjudications.',
                original_input_file_hash_status='Declared input hash is retained; the original serialized input file is not in this package.',
                configuration=dict(options=config.get('options'),think=config.get('think'),workers=config.get('workers'),runtime=config.get('runtime'),input_sha256=config.get('input_sha256'),prompt_sha256=hashlib.sha256(config['system'].encode()).hexdigest(),fingerprint=config['fingerprint']))
    return report,rows,judgments


def _package_bytes(path):
    path=Path(path)
    if path.is_dir():
        result={name:(path/name).read_bytes() for name in PACKAGE_FILES}
        result.update({name:(path/name).read_bytes() for name in OPTIONAL_FILES if (path/name).is_file()})
    else:
        with zipfile.ZipFile(path) as z:
            names={}
            for name in z.namelist():
                p=Path(name)
                if p.is_absolute() or '..' in p.parts: raise ValueError('Unsafe package member name')
                if p.name in PACKAGE_FILES+OPTIONAL_FILES:
                    if p.name in names: raise ValueError('Duplicate package member basename')
                    names[p.name]=name
            result={name:z.read(names[name]) for name in PACKAGE_FILES}
            result.update({name:z.read(names[name]) for name in OPTIONAL_FILES if name in names})
    return result


def verify_score_workbook(content,authority,rows,label):
    """Check a new workbook's machine scores without changing the frozen input."""
    snapshot=read_authority(content,expected_answers=len(authority['records']),expected_rows=len(rows))
    for field in ['records','mapping','humans','adjudications']:
        if snapshot[field]!=authority[field]:raise ValueError(f'{label} changes frozen {field}')
    if 'human_sources' in authority and snapshot['human_sources']!=authority['human_sources']:
        raise ValueError(f'{label} changes frozen human sources')
    wb=openpyxl.load_workbook(io.BytesIO(content),read_only=False,data_only=True)
    formulas=openpyxl.load_workbook(io.BytesIO(content),read_only=False,data_only=False)
    try:
        for item in rows:
            r=item['row'];s=wb['Qwen_check'];raw=formulas['Qwen_check']
            if (s.cell(r,1).value,s.cell(r,2).value,s.cell(r,5).value)!=(item['judgment'],item['id'],item['target']): raise ValueError(f'{label} identity differs at row {r}')
            for col in ['A','B','E']:
                if raw[f'{col}{r}'].data_type=='f' and raw[f'{col}{r}'].value!=f'=Agreement!{col}{r}':raise ValueError(f'{label} has a changed identity formula at row {r}')
            for column,field in [(10,'AB'),(11,'BA')]:
                if raw.cell(r,column).data_type=='f':raise ValueError(f'{label} machine scores must be literal values at row {r}/{field}')
                if binary(s.cell(r,column).value)!=item[field]: raise ValueError(f'{label} score differs at row {r}/{field}')
            if binary(s.cell(r,9).value,optional=True)!=item['human']: raise ValueError(f'{label} cached consensus differs at row {r}')
    finally:wb.close();formulas.close()


def audit_package(package,workbook, *, score_workbook=None):
    content=_package_bytes(package);data=literal_json(content['results.json'].decode());scores=literal_json(content['score_rows.json'].decode())
    summary=literal_json(content['summary.json'].decode());status=literal_json(content['run_status.json'].decode())
    authority=read_authority(workbook)
    report,rows,judgments=audit_records(data,authority,score_rows=scores,summary=summary,run_status=status,system_prompt=content['judge_prompt.txt'].decode())
    script_hash=hashlib.sha256(content['judge.py']).hexdigest()
    if status.get('script_sha256')!=script_hash: raise ValueError('Packaged script hash differs from run status')
    if 'Tokenify_blind_review_Qwen_run3.xlsx' in content:
        verify_score_workbook(content['Tokenify_blind_review_Qwen_run3.xlsx'],authority,rows,'Packaged workbook')
    if score_workbook is not None:
        verify_score_workbook(Path(score_workbook).read_bytes(),authority,rows,'Current score workbook')
    report['source_sha256']={**{name:hashlib.sha256(value).hexdigest() for name,value in content.items()},'authoritative_workbook':authority['sha256']}
    if score_workbook is not None:report['source_sha256']['current_score_workbook']=hashlib.sha256(Path(score_workbook).read_bytes()).hexdigest()
    report['packaged_workbook_scores_verified']='Tokenify_blind_review_Qwen_run3.xlsx' in content
    report['current_score_workbook_verified']=score_workbook is not None;report['packaged_script_hash_verified']=True
    return report,rows,judgments


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package',type=Path,required=True);parser.add_argument('--workbook',type=Path,required=True,help='Frozen input workbook matching the run source hash')
    parser.add_argument('--score-workbook',type=Path,help='Optional current central workbook with the verified run scores pasted into J/K')
    parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args(argv);report,rows,judgments=audit_package(args.package,args.workbook,score_workbook=args.score_workbook)
    paths={args.output_dir/name for name in ['run_package_audit.json','target_scores.csv','judgments.csv']}
    inputs={args.workbook.resolve(),args.package.resolve()}
    if args.score_workbook:inputs.add(args.score_workbook.resolve())
    if any(p.resolve() in inputs for p in paths):raise ValueError('Output would overwrite an input')
    args.output_dir.mkdir(parents=True,exist_ok=True)
    (args.output_dir/'run_package_audit.json').write_text(json.dumps(report,indent=2)+'\n')
    for name,values in [('target_scores.csv',rows),('judgments.csv',judgments)]:
        with (args.output_dir/name).open('w',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=list(values[0]));writer.writeheader();writer.writerows(values)
    print(json.dumps(report,indent=2));return 0


if __name__=='__main__':raise SystemExit(main())
