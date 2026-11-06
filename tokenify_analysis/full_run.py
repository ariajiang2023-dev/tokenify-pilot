"""Offline RQ1–RQ3 analysis of normalized full-generation outputs."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import random
import statistics

from . import full_run_io as fio

MEANING_METRICS=('chose_target','mentioned_target','listed_several')
CONTROL_METRICS=('correct','n_words','reading_grade_fk','names_field','stereotyped')
BEHAVIORS=('picks_one','lists_several','asks','no_answer','asks_or_no_answer')


def _mean(values):
    return statistics.mean(values) if values else None


def _rng(seed,namespace):
    digest=hashlib.sha256(f'{seed}:{namespace}'.encode()).digest()
    return random.Random(int.from_bytes(digest,'big'))


def _quantile(values,p):
    values=sorted(values); pos=(len(values)-1)*p; lo=math.floor(pos); hi=math.ceil(pos)
    return values[lo]+(values[hi]-values[lo])*(pos-lo)


def cluster_interval(clusters, *, bootstrap=2000, seed=42, namespace=''):
    """Percentile cluster bootstrap, retaining all rows from each drawn group."""
    keys=sorted(clusters)
    if len(keys)<2: return None,None
    rng=_rng(seed,namespace); draws=[]
    for _ in range(bootstrap):
        values=[x for _ in keys for x in clusters[rng.choice(keys)]]
        draws.append(statistics.mean(values))
    return _quantile(draws,.025),_quantile(draws,.975)


def _paired_test(baseline,cued):
    differences=[b-a for a,b in zip(baseline,cued)]
    if len(differences)<2: return None,None,'fewer_than_two_terms'
    variance=statistics.variance(differences)
    if variance==0:
        if differences[0]==0: return 0.0,1.0,'identical_zero_differences'
        return None,0.0,'constant_nonzero_difference_t_infinite'
    from scipy.stats import ttest_rel
    result=ttest_rel(cued,baseline)
    return float(result.statistic),float(result.pvalue),'available'


def _label(row):
    a,b=row.get('judge_ab'),row.get('judge_ba')
    return a if a is not None and a==b else None


def _meaning_value(row,target,metric):
    label=_label(row)
    if label is None: return None
    if metric=='chose_target': return int(label==target)
    if metric=='listed_several': return int(label=='multiple')
    a,b=(row.get(f'mentioned_{target.lower()}_{order}') for order in ['ab','ba'])
    return int(a) if a is not None and a==b else None


def _term_analysis(rows,expected_contexts,bootstrap,seed):
    terms=sorted({(r['model'],r['term_id']) for r in rows})
    models=sorted({m for m,t in terms}); cells={}; fields={}; incomplete=[]
    for r in rows:
        fields[(r['model'],r['term_id'])]={'A':r['field_a'],'B':r['field_b']}
        targets='AB' if r['condition']=='C0' else [r['target']]
        for target in targets:
            key=(r['model'],r['term_id'],r['condition'],target,r['context_index'])
            if key in cells: raise ValueError(f'Duplicate term/context cell under different item IDs: {key}')
            cells[key]=r
    required={(t,c) for t in 'AB' for c in range(expected_contexts)}
    per_term=[]; complete={}
    for model,term in terms:
        for condition in ['C0','C1','C2']:
            for metric in MEANING_METRICS:
                found={}
                for target,context in sorted(required):
                    row=cells.get((model,term,condition,target,context))
                    value=_meaning_value(row,target,metric) if row else None
                    if value is not None: found[(target,context)]=value
                status='complete' if set(found)==required else 'incomplete'
                mean=_mean(list(found.values())) if status=='complete' else None
                row=dict(model=model,term_id=term,condition=condition,metric=metric,
                         expected_target_cells=len(required),available_target_cells=len(found),mean=mean,status=status)
                per_term.append(row)
                if status=='complete': complete[(model,term,condition,metric)]=mean
                else: incomplete.append({k:row[k] for k in ['model','term_id','condition','metric','expected_target_cells','available_target_cells']})
    rates=[]; comparisons=[]
    for model in models:
        model_terms=[t for m,t in terms if m==model]
        for metric in MEANING_METRICS:
            for condition in ['C0','C1','C2']:
                values=[complete[(model,t,condition,metric)] for t in model_terms if (model,t,condition,metric) in complete]
                rates.append(dict(model=model,condition=condition,metric=metric,n_terms=len(values),rate=_mean(values),
                                  excluded_terms=[t for t in model_terms if (model,t,condition,metric) not in complete]))
            for condition in ['C1','C2']:
                eligible=[t for t in model_terms if all((model,t,c,metric) in complete for c in ['C0',condition])]
                base=[complete[(model,t,'C0',metric)] for t in eligible]
                cued=[complete[(model,t,condition,metric)] for t in eligible]
                tstat,p,status=_paired_test(base,cued)
                comparisons.append(dict(model=model,condition=condition,metric=metric,n_terms=len(eligible),baseline_mean=_mean(base),condition_mean=_mean(cued),
                                        mean_difference=_mean([b-a for a,b in zip(base,cued)]),t_statistic=tstat,p_value_two_sided=p,test_status=status,
                                        excluded_terms=[t for t in model_terms if t not in eligible]))
    field_results=[]
    for model in models:
        all_fields=sorted({v for (m,t),mapping in fields.items() if m==model for v in mapping.values()})
        for condition in ['C1','C2']:
            for field in all_fields:
                clusters={}
                for m,term in terms:
                    if m!=model: continue
                    targets=[t for t,v in fields[(m,term)].items() if v==field]
                    values=[]
                    for target in targets:
                        current=[]
                        for context in range(expected_contexts):
                            row=cells.get((model,term,condition,target,context))
                            current.append(_meaning_value(row,target,'chose_target') if row else None)
                        if all(x is not None for x in current): values.extend(current)
                    if values: clusters[term]=[statistics.mean(values)]
                low,high=cluster_interval(clusters,bootstrap=bootstrap,seed=seed,namespace=f'field:{model}:{condition}:{field}')
                field_results.append(dict(model=model,condition=condition,field=field,n_terms=len(clusters),rate=_mean([x[0] for x in clusters.values()]),ci_low=low,ci_high=high))
    return per_term,rates,comparisons,field_results,incomplete


def _control_analysis(rows,bootstrap,seed):
    grouped=defaultdict(list)
    for row in rows: grouped[(row['model'],row['item_id'])].append(row)
    pairs=defaultdict(list); missing=[]; changes=[]
    for (model,item),group in sorted(grouped.items()):
        baselines=[r for r in group if r['condition']=='C0']
        if len(baselines)>1: raise ValueError(f'Multiple C0 control baselines: {model}/{item}')
        baseline=baselines[0] if baselines else None
        for condition in ['C1','C2','C3']:
            cued=[r for r in group if r['condition']==condition]
            expected=set(group[0]['expected_cue_fields'][condition])
            observed={r['cue_field'] for r in cued}
            missing_fields=sorted(expected-observed)
            if baseline is None or missing_fields:
                for metric in CONTROL_METRICS: pairs[(model,condition,metric)]
                missing.append(dict(model=model,item_id=item,condition=condition,metric=None,
                                    reason='missing_baseline' if baseline is None else 'incomplete_cue_coverage',
                                    missing_cue_fields=missing_fields))
                changes.append(dict(model=model,item_id=item,condition=condition,n_cued_responses=len(cued),
                                    expected_cue_fields=sorted(expected),missing_cue_fields=missing_fields,
                                    status='incomplete_design'))
                continue
            for metric in CONTROL_METRICS:
                b=baseline.get(metric) if baseline else None
                available=baseline is not None and b is not None and all(r.get(metric) is not None for r in cued)
                if not available:
                    missing.append(dict(model=model,item_id=item,condition=condition,metric=metric,reason='missing_baseline_or_metric'))
                    pairs[(model,condition,metric)]
                    continue
                difference=statistics.mean(float(r[metric]) for r in cued)-float(b)
                pairs[(model,condition,metric)].append(dict(item_id=item,author_id=baseline['author_id'],difference=difference))
            counts=Counter(r['change_vs_c0'] for r in cued if r.get('change_vs_c0') is not None)
            changes.append(dict(model=model,item_id=item,condition=condition,n_cued_responses=len(cued),n_missing_change_labels=len(cued)-sum(counts.values()),**{k:counts[k] for k in sorted(fio.CHANGE_LABELS)}))
    results=[]
    for (model,condition,metric),values in sorted(pairs.items()):
        clusters=defaultdict(list)
        for value in values: clusters[value['author_id']].append(value['difference'])
        low,high=cluster_interval(clusters,bootstrap=bootstrap,seed=seed,namespace=f'rq2:{model}:{condition}:{metric}')
        results.append(dict(model=model,condition=condition,metric=metric,n_items=len(values),n_authors=len(clusters),
                            mean_difference=_mean([x['difference'] for x in values]),ci_low=low,ci_high=high,
                            ci_status='available' if len(clusters)>=2 else 'fewer_than_two_authors'))
    return results,changes,missing


def _behavior(row):
    label=_label(row)
    if label is None: return None
    if label in {'A','B','other'}: return 'picks_one'
    if label=='multiple': return 'lists_several'
    asked=row.get('asked_clarification')
    return 'asks' if asked is True else ('no_answer' if asked is False else 'asks_or_no_answer')


def _default_field(row):
    label=_label(row)
    if label in {'A','B'}: return row['field_'+label.lower()]
    if label=='other': return row.get('other_field') or 'other_unspecified'
    if label=='multiple': return 'multiple'
    if label=='no_answer': return 'no_answer'
    return None


def _no_cue_analysis(rows,expected_contexts,bootstrap,seed):
    groups=defaultdict(list)
    for row in rows:
        if row['condition']=='C0': groups[(row['model'],row['term_id'])].append(row)
    model_terms=defaultdict(list); defaults=defaultdict(dict); incomplete=[]
    for (model,term),group in sorted(groups.items()):
        if {r['context_index'] for r in group}!=set(range(expected_contexts)) or any(_label(r) is None for r in group):
            incomplete.append(dict(model=model,term_id=term,reason='missing_context_or_unresolved_order')); continue
        counts=Counter(_behavior(r) for r in group)
        model_terms[model].append((term,{k:counts[k]/expected_contexts for k in BEHAVIORS}))
        defaults[model][term]=Counter(_default_field(r) for r in group)
    results=[]; default_results=[]
    for model,terms in sorted(model_terms.items()):
        for behavior in BEHAVIORS:
            clusters={t:[values[behavior]] for t,values in terms}
            low,high=cluster_interval(clusters,bootstrap=bootstrap,seed=seed,namespace=f'rq3:{model}:{behavior}')
            results.append(dict(model=model,behavior=behavior,n_terms=len(terms),rate=statistics.mean(v[behavior] for t,v in terms),ci_low=low,ci_high=high))
        fields=sorted({field for counts in defaults[model].values() for field in counts})
        for field in fields:
            values=[counts[field]/expected_contexts for counts in defaults[model].values()]
            default_results.append(dict(model=model,default_field=field,n_terms=len(terms),rate=statistics.mean(values)))
    return results,default_results,incomplete


def _variability(rows):
    groups=defaultdict(list)
    for row in rows:
        if row['set']=='terms' and row['split']=='main' and row['response_phase']=='first' and row['condition'] in {'C0','C1','C2'}:
            groups[(row['model'],row['item_id'],row['condition'],row.get('cue_field'))].append(row)
    out=[]
    for (model,item,condition,field),group in sorted(groups.items(),key=lambda x:str(x[0])):
        repeats=[r for r in group if r['repeat_index']>0]
        if not repeats: continue
        baseline=[r for r in group if r['repeat_index']==0]
        choices=[_label(r) for r in repeats if _label(r) is not None]
        counts=Counter(choices); initial=_label(baseline[0]) if baseline else None
        out.append(dict(model=model,item_id=item,condition=condition,cue_field=field,n_repeats=len(repeats),n_order_consistent=len(choices),
                        expected_repeats=5,complete_five=len(repeats)==5,temperatures=sorted({r['temperature'] for r in repeats if r['temperature'] is not None}),
                        choice_counts=dict(sorted(counts.items())),baseline_choice=initial,
                        fraction_different_from_baseline=sum(c!=initial for c in choices)/len(choices) if choices and initial is not None else None))
    return out


def analyze(records, *, expected_contexts=6, bootstrap=2000, seed=42):
    if expected_contexts<1 or bootstrap<100: raise ValueError('expected_contexts >=1 and bootstrap >=100 required')
    rows=sorted(records,key=lambda r:r['response_id'])
    excluded=Counter(fio.exclusion_reason(r) for r in rows if fio.exclusion_reason(r))
    main=[r for r in rows if fio.exclusion_reason(r) is None]
    terms=[r for r in main if r['set']=='terms']; controls=[r for r in main if r['set']=='expertqa']
    if any(r['context_index']>=expected_contexts for r in terms): raise ValueError('context_index exceeds expected_contexts')
    per_term,rates,comparisons,fields,term_missing=_term_analysis(terms,expected_contexts,bootstrap,seed)
    rq2,changes,control_missing=_control_analysis(controls,bootstrap,seed)
    rq3,defaults,nocue_missing=_no_cue_analysis(terms,expected_contexts,bootstrap,seed)
    coverage=dict(total_responses=len(rows),main_responses=len(main),excluded_counts=dict(sorted(excluded.items())),
                  order_conflicts=[r['response_id'] for r in terms if r['judge_ab'] is not None and r['judge_ba'] is not None and r['judge_ab']!=r['judge_ba']],
                  missing_judgments=[r['response_id'] for r in terms if r['judge_ab'] is None or r['judge_ba'] is None],
                  incomplete_rq1_groups=term_missing,missing_rq2_pairs=control_missing,incomplete_rq3_terms=nocue_missing)
    return dict(coverage=coverage,rq1_by_term=per_term,rq1_rates=rates,rq1_comparisons=comparisons,rq1_fields=fields,
                rq2=rq2,rq2_change_categories=changes,rq3=rq3,rq3_default_fields=defaults,variability=_variability(rows))


def _write_csv(path,rows):
    if not rows:
        path.write_text(''); return
    keys=list(dict.fromkeys(k for row in rows for k in row))
    with path.open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=keys);writer.writeheader()
        writer.writerows({k:json.dumps(v,sort_keys=True) if isinstance(v,(list,dict)) else v for k,v in row.items()} for row in rows)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,required=True)
    parser.add_argument('--judgments',type=Path,help='Optional JSONL containing normalized or displayed-order judgments')
    parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--expected-contexts',type=int,default=6,help='Question plus five contexts = 6 for the planned full run')
    parser.add_argument('--bootstrap',type=int,default=2000)
    parser.add_argument('--seed',type=int,default=42)
    parser.add_argument('--compute-text-metrics',action='store_true',help='Recompute word count and FK from response_text using pinned resources')
    args=parser.parse_args(argv)
    rows=fio.load_records(args.input)
    sources={str(args.input):hashlib.sha256(args.input.read_bytes()).hexdigest()}
    if args.judgments:
        judgments=[json.loads(line) for line in args.judgments.read_text().splitlines() if line.strip()]
        rows=fio.join_judgments(rows,judgments); sources[str(args.judgments)]=hashlib.sha256(args.judgments.read_bytes()).hexdigest()
    metric_metadata=None
    if args.compute_text_metrics:
        from .metrics import measure_text,_fk_runtime_metadata
        metric_metadata=_fk_runtime_metadata()
        for row in rows:
            measured=measure_text(row['response_text'])
            row['n_words']=measured['n_words'];row['reading_grade_fk']=measured['reading_grade_fk']
    result=analyze(rows,expected_contexts=args.expected_contexts,bootstrap=args.bootstrap,seed=args.seed)
    output_paths={args.output_dir/(key+'.csv') for key in result if key!='coverage'}
    output_paths.update({args.output_dir/'coverage.json',args.output_dir/'analysis_metadata.json'})
    source_paths={args.input.resolve()}
    if args.judgments: source_paths.add(args.judgments.resolve())
    if any(path.resolve() in source_paths for path in output_paths): raise ValueError('Output would overwrite an input file')
    args.output_dir.mkdir(parents=True,exist_ok=True)
    for key,value in result.items():
        if key!='coverage': _write_csv(args.output_dir/(key+'.csv'),value)
    (args.output_dir/'coverage.json').write_text(json.dumps(result['coverage'],indent=2)+'\n')
    metadata=dict(input_sha256=sources,seed=args.seed,bootstrap_replicates=args.bootstrap,expected_contexts=args.expected_contexts,
                  data_origins=sorted({r.get('data_origin','unspecified') for r in rows}),
                  numpy_version=importlib.metadata.version('numpy'),scipy_version=importlib.metadata.version('scipy'),text_metrics=metric_metadata,
                  assumptions=['First responses, main split, repeat_index=0 only. C2-R is excluded.',
                               'RQ1: complete identical target/context coverage, equal term weight, two-sided paired t tests.',
                               'RQ2: complete metric pairs, equal cue weight within item, percentile author-cluster bootstrap.',
                               'RQ3: complete C0 contexts, equal term weight, percentile term-cluster bootstrap.',
                               'Missing annotations and unresolved reference orders remain unavailable. No human labels are generated.'],
                  publication_status='Analysis outputs require completed generation and independent judge validation before confirmatory interpretation.')
    (args.output_dir/'analysis_metadata.json').write_text(json.dumps(metadata,indent=2)+'\n')
    print(json.dumps(dict(main_responses=result['coverage']['main_responses'],order_conflicts=len(result['coverage']['order_conflicts']),output_dir=str(args.output_dir))))
    return 0


if __name__=='__main__': raise SystemExit(main())
