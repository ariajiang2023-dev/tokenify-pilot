"""Validated, stable-ID interchange for full generation and judgment outputs."""
from __future__ import annotations

from collections import Counter
import csv
import json
import math
from pathlib import Path

LABELS = {'A', 'B', 'other', 'multiple', 'no_answer'}
CONDITIONS = {'C0', 'C1', 'C2', 'C3', 'C2-R'}
CHANGE_LABELS = {'none', 'examples or difficulty only', 'drops a key point', 'contradicts a key point'}
BOOLS = {'names_field', 'stereotyped', 'asked_clarification', 'mentioned_a_ab', 'mentioned_a_ba', 'mentioned_b_ab', 'mentioned_b_ba'}
NUMBERS = {'n_words', 'reading_grade_fk', 'temperature'}
ALIASES = {'term / question':'question', 'context #':'context_index', 'cue field':'cue_field',
           'target (A/B/both/NA)':'target', 'judge A,B order':'judge_ab', 'judge B,A order':'judge_ba',
           'eqa correct (1/0)':'correct', 'eqa change vs. C0':'change_vs_c0',
           'names field?':'names_field', 'stereotyped?':'stereotyped', 'reading grade':'reading_grade_fk'}
REQUIRED = {'response_id','request_id','model','set','item_id','question','response_text','response_phase','repeat_index','split','condition'}


def _missing(value):
    return value is None or value == ''


def _boolean(value, field):
    if _missing(value): return None
    if type(value) is bool: return value
    if isinstance(value,str) and value.lower() in {'true','false','yes','no'}:
        return value.lower() in {'true','yes'}
    raise ValueError(f'{field} requires true/false or missing, got {value!r}')


def _number(value, field, integer=False):
    if _missing(value): return None
    if type(value) is bool: raise ValueError(f'{field} requires a number')
    try: number=float(value)
    except (TypeError,ValueError) as exc: raise ValueError(f'{field} requires a number') from exc
    if not math.isfinite(number) or integer and not number.is_integer(): raise ValueError(f'Invalid {field}: {value!r}')
    return int(number) if integer else number


def normalize_records(records):
    """Validate primary design identities; missing annotations stay None."""
    out=[]; ids=set(); requests=set(); cells=set(); items={}; term_references={}; control_designs={}; term_questions={}
    for source in records:
        row={ALIASES.get(k,k):v for k,v in source.items()}
        missing=[k for k in REQUIRED if k not in row or _missing(row[k])]
        if missing: raise ValueError(f'Missing required fields: {sorted(missing)}')
        for field in ['response_id','request_id','model','item_id']:
            if not isinstance(row[field],str) or not row[field].strip(): raise ValueError(f'Invalid {field}')
        if row['response_id'] in ids: raise ValueError(f'Duplicate response_id: {row["response_id"]}')
        ids.add(row['response_id'])
        if row['request_id'] in requests: raise ValueError(f'Duplicate request_id: {row["request_id"]}')
        requests.add(row['request_id'])
        row['set']={'term':'terms','Review':'terms','ExpertQA_scoring':'expertqa','controls':'expertqa'}.get(row['set'],row['set'])
        if row['set'] not in {'terms','expertqa'}: raise ValueError('set must be terms or expertqa')
        if row['condition'] not in CONDITIONS: raise ValueError('Unknown condition')
        if row['response_phase'] not in {'first','followup'}: raise ValueError('response_phase must be first or followup')
        if row['split'] not in {'main','pilot'}: raise ValueError('split must be main or pilot')
        row['repeat_index']=_number(row['repeat_index'],'repeat_index',True)
        if row['repeat_index'] < 0: raise ValueError('repeat_index must be nonnegative')
        for field in BOOLS: row[field]=_boolean(row.get(field),field)
        for field in NUMBERS: row[field]=_number(row.get(field),field,field=='n_words')
        if row['n_words'] is not None and row['n_words']<0: raise ValueError('n_words must be nonnegative')
        if row['temperature'] is not None and row['temperature']<0: raise ValueError('temperature must be nonnegative')
        row['correct']=_number(row.get('correct'),'correct',True)
        if row['correct'] not in {None,0,1}: raise ValueError('correct must be 1, 0 or missing')
        row['change_vs_c0']=row.get('change_vs_c0') or None
        if row['change_vs_c0'] not in CHANGE_LABELS|{None}: raise ValueError('Invalid change_vs_c0')
        for field in ['judge_ab','judge_ba']:
            row[field]=row.get(field) or None
            if row[field] not in LABELS|{None}: raise ValueError(f'Invalid {field}; use normalized meaning identities')
        if not isinstance(row['question'],str) or not isinstance(row['response_text'],str): raise ValueError('question and response_text must be strings')
        if row['condition']=='C0':
            if row.get('cue_field') not in {None,''}: raise ValueError('C0 cannot have a cue_field')
            row['cue_field']=None
        elif not isinstance(row.get('cue_field'),str) or not row['cue_field'].strip(): raise ValueError('Cued rows require cue_field')
        if row['set']=='terms':
            for field in ['term_id','field_a','field_b','reference_a','reference_b']:
                if not isinstance(row.get(field),str) or not row[field].strip(): raise ValueError(f'Term rows require {field}')
            if row['field_a']==row['field_b']: raise ValueError('Two term fields must differ')
            row['context_index']=_number(row.get('context_index'),'context_index',True)
            if row['context_index'] is None or row['context_index']<0: raise ValueError('Term rows require nonnegative context_index')
            if row['condition']=='C3': raise ValueError('C3 is for controls only')
            expected={'C0':{'both'},'C1':{'A','B'},'C2':{'A','B'},'C2-R':{'A','B'}}[row['condition']]
            if row.get('target') not in expected: raise ValueError('Invalid target for term condition')
            if row['condition']!='C0' and row['cue_field']!=row['field_'+row['target'].lower()]: raise ValueError('cue_field differs from target reference field')
            question_key=(row['term_id'],row['context_index'])
            question_identity=(row['item_id'],row['question'])
            if question_key in term_questions and term_questions[question_key]!=question_identity:
                raise ValueError(f'Inconsistent term/context question or item ID: {question_key}')
            term_questions[question_key]=question_identity
            term_identity=(row['field_a'],row['field_b'],row['reference_a'],row['reference_b'])
            if row['term_id'] in term_references and term_references[row['term_id']]!=term_identity:
                raise ValueError(f'Inconsistent term references across contexts: {row["term_id"]}')
            term_references[row['term_id']]=term_identity
            identity=(row['term_id'],row['context_index'],row['question'],*term_identity)
        else:
            if row.get('author_id') is None or str(row['author_id']).strip()=='': raise ValueError('ExpertQA rows require author_id')
            row['author_id']=str(row['author_id'])
            design=row.get('expected_cue_fields')
            if isinstance(design,str):
                try: design=json.loads(design)
                except ValueError as exc: raise ValueError('expected_cue_fields must be a JSON object') from exc
            if not isinstance(design,dict) or set(design)!={'C1','C2','C3'}:
                raise ValueError('ExpertQA rows require expected_cue_fields for C1, C2, C3 from the frozen manifest')
            for condition,field_list in design.items():
                if not isinstance(field_list,list) or not field_list or any(not isinstance(f,str) or not f.strip() for f in field_list) or len(field_list)!=len(set(field_list)):
                    raise ValueError('expected_cue_fields lists must be nonempty, unique field names')
            row['expected_cue_fields']={c:sorted(design[c]) for c in sorted(design)}
            design_key=(row['model'],row['item_id'])
            if design_key in control_designs and control_designs[design_key]!=row['expected_cue_fields']:
                raise ValueError('Inconsistent expected_cue_fields for one model/item')
            control_designs[design_key]=row['expected_cue_fields']
            if row['condition']!='C0' and row['cue_field'] not in row['expected_cue_fields'][row['condition']]:
                raise ValueError('Control cue_field is not in declared expected_cue_fields')
            row['context_index']=None
            if row['condition']=='C2-R': raise ValueError('C2-R is a term pilot diagnostic only')
            if row.get('target') not in {None,'','NA'}: raise ValueError('ExpertQA target must be NA')
            row['target']='NA'
            row['reference_answer']=row.get('reference_answer') or None
            identity=(row['author_id'],row['question'],row['reference_answer'])
        item_key=(row['set'],row['item_id'])
        if item_key in items and items[item_key]!=identity: raise ValueError(f'Inconsistent item identity: {item_key}')
        items[item_key]=identity
        cell=(row['model'],row['set'],row['item_id'],row['condition'],row.get('cue_field'),row['response_phase'],row['repeat_index'],row['split'])
        if cell in cells: raise ValueError(f'Duplicate design cell: {cell}')
        cells.add(cell)
        out.append(row)
    return sorted(out,key=lambda r:r['response_id'])


def load_records(path):
    path=Path(path)
    if path.suffix.lower()=='.csv':
        with path.open(newline='',encoding='utf-8-sig') as f: rows=list(csv.DictReader(f))
    elif path.suffix.lower() in {'.jsonl','.ndjson'}:
        rows=[json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    else: raise ValueError('Input must be JSONL or CSV')
    if not rows: raise ValueError('Empty response input')
    return normalize_records(rows)


def join_judgments(records,judgments):
    """Join raw displayed A/B judgments by response ID and reference order."""
    out={r['response_id']:dict(r) for r in records}; seen=set()
    for judge in judgments:
        rid=judge['response_id']; order=judge['order']
        if judge.get('status','ok')!='ok': raise ValueError(f'Judgment status is not ok: {rid}')
        if rid not in out: raise ValueError(f'Unknown response in judgments: {rid}')
        if order not in {'AB','BA'} or (rid,order) in seen: raise ValueError('Duplicate or invalid judgment order')
        seen.add((rid,order)); row=out[rid]
        remap={'A':'A','B':'B'} if order=='AB' else {'A':'B','B':'A'}
        if 'selected' in judge:
            choice=judge['selected']; mentioned=judge.get('mentioned',{})
        else:
            raw=judge.get('choice'); choice=remap.get(raw,raw)
            mentioned={remap[s]:judge.get('mentioned_'+s) for s in 'AB'}
        if choice not in LABELS: raise ValueError('Invalid judge choice')
        suffix=order.lower()
        if row.get('judge_'+suffix) is not None: raise ValueError(f'Judgment would overwrite an existing label: {rid}/{order}')
        row['judge_'+suffix]=choice
        for sense in 'AB': row['mentioned_'+sense.lower()+'_'+suffix]=_boolean(mentioned.get(sense),'mentioned_'+sense)
    return normalize_records(list(out.values()))


def exclusion_reason(row):
    if row['split']=='pilot': return 'pilot'
    if row['response_phase']=='followup': return 'followup'
    if row['repeat_index']>0: return 'repeat'
    if row['condition']=='C2-R': return 'roommate'
    return None
