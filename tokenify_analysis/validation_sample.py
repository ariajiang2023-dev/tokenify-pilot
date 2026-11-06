"""Seeded stratified validation sampling and blank, blinded coding sheets."""
from __future__ import annotations

import argparse
from collections import Counter,defaultdict
import csv
import hashlib
import json
from pathlib import Path
import random

from .full_run_io import exclusion_reason,load_records


def _allocate(strata,n):
    keys=sorted(strata)
    if n<len(keys): raise ValueError('Sample size is smaller than the number of nonempty strata')
    allocation={k:1 for k in keys}
    for _ in range(n-len(keys)):
        eligible=[k for k in keys if allocation[k]<len(strata[k])]
        key=max(eligible,key=lambda k:(n*len(strata[k])/sum(map(len,strata.values()))-allocation[k],tuple(k)))
        allocation[key]+=1
    return allocation


def make_sample(records,*,n=200,seed=42):
    rows=[r for r in records if exclusion_reason(r) is None]
    if n<2 or n>len(rows): raise ValueError(f'Requested {n} responses; {len(rows)} available')
    strata=defaultdict(list)
    for row in rows: strata[(row['model'],row['condition'],row['set'])].append(row)
    if any(r['set']=='expertqa' and not str(r.get('reference_answer') or '').strip() for r in rows):
        raise ValueError('ExpertQA coding sheets require reference_answer before sampling')
    allocation=_allocate(strata,n); selected=[]; rng=random.Random(seed)
    for key in sorted(strata):
        group=sorted(strata[key],key=lambda r:r['response_id'])
        selected.extend(rng.sample(group,allocation[key]))
    groups=defaultdict(list)
    for row in selected:
        group='term:'+row['term_id'] if row['set']=='terms' else 'author:'+row['author_id']
        groups[group].append(row)
    if len(groups)<2: raise ValueError('Need at least two independent term/author groups for a development/held-out split')
    group_order=sorted(groups); rng.shuffle(group_order)
    assignment={}; counts={'development':0,'held_out':0}
    for group in group_order:
        split=min(counts,key=lambda k:(counts[k],k))
        assignment[group]=split;counts[split]+=len(groups[group])
    rng.shuffle(selected)
    blind=[]; private=[]
    for row in selected:
        sample_id=hashlib.sha256(f'tokenify-validation:{seed}:{row["response_id"]}'.encode()).hexdigest()[:20]
        blind.append(dict(sample_id=sample_id,question=row['question'],reference_a=row.get('reference_a') or '',reference_b=row.get('reference_b') or '',
                          reference_answer=row.get('reference_answer') or '',response_text=row['response_text'],human_meaning='',human_correct='',human_notes=''))
        group='term:'+row['term_id'] if row['set']=='terms' else 'author:'+row['author_id']
        private.append(dict(sample_id=sample_id,response_id=row['response_id'],model=row['model'],condition=row['condition'],set=row['set'],
                            item_id=row['item_id'],group_id=group,split=assignment[group],
                            stratum_available=len(strata[(row['model'],row['condition'],row['set'])]),
                            stratum_sampled=allocation[(row['model'],row['condition'],row['set'])]))
    return dict(blind_rows=blind,private_key=private,metadata=dict(seed=seed,n=n,
                strata=[dict(model=k[0],condition=k[1],set=k[2],available=len(strata[k]),sampled=allocation[k]) for k in sorted(strata)],
                development_responses=counts['development'],held_out_responses=counts['held_out'],
                groups=len(groups),split_unit='term for term responses; author for ExpertQA, shared across models and conditions',
                split_note='Group isolation takes priority over an exactly 100/100 response split. Freeze this key before tuning.'))


def _csv(path,rows):
    with path.open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,required=True);parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--n',type=int,default=200);parser.add_argument('--seed',type=int,default=42)
    args=parser.parse_args(argv)
    if args.output_dir.exists() and any(args.output_dir.iterdir()): raise ValueError('Output directory is not empty; do not overwrite a frozen sample')
    result=make_sample(load_records(args.input),n=args.n,seed=args.seed)
    blind=args.output_dir/'blinded'; private=args.output_dir/'private'
    blind.mkdir(parents=True,exist_ok=True);private.mkdir()
    for name in ['coder_1','coder_2','adjudicator']: _csv(blind/(name+'.csv'),result['blind_rows'])
    _csv(private/'sample_key.csv',result['private_key'])
    metadata={**result['metadata'],'input_sha256':hashlib.sha256(args.input.read_bytes()).hexdigest()}
    (private/'sample_metadata.json').write_text(json.dumps(metadata,indent=2)+'\n')
    print(json.dumps(dict(n=args.n,blinded=str(blind),private_key=str(private/'sample_key.csv'))))
    return 0


if __name__=='__main__': raise SystemExit(main())
