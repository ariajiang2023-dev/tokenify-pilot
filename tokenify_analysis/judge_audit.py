"""Offline audit of preserved Qwen judgments. Never changes annotations."""

import argparse
import csv
import hashlib
import json
from pathlib import Path
import re
import zipfile

import openpyxl

ROOT = Path(__file__).resolve().parents[1]

LABELS = {'A', 'B', 'other', 'multiple', 'no_answer'}


def agreement(human, judge):
    if len(human) != len(judge) or any(type(x) is not int or x not in (0, 1) for x in [*human, *judge]):
        raise ValueError('Agreement requires paired, nonmissing binary integer labels')
    n = len(human)
    if not n:
        return {'n': 0, 'agreement': None, 'kappa': None}
    observed = sum(a == b for a, b in zip(human, judge)) / n
    p, q = sum(human) / n, sum(judge) / n
    chance = p * q + (1 - p) * (1 - q)
    return {'n': n, 'agreement': observed, 'kappa': (observed - chance) / (1 - chance) if chance < 1 else None}


def audit_records(records, human_by_row):
    judgments, mapping = records['judgments'], records['mapping']
    if not judgments or not mapping:
        raise ValueError('Empty records')
    by_key, all_ids, attempts, flags = {}, set(), 0, []
    for j in judgments:
        key = (j['id'], j['order'])
        if key in by_key:
            raise ValueError(f'Duplicate request {key}')
        if j['order'] not in ('AB', 'BA') or j['status'] != 'ok':
            raise ValueError(f'Invalid order/status {key}')
        if j['selected'] not in LABELS or not j['attempts']:
            raise ValueError(f'Invalid choice/attempts {key}')
        raw = json.loads(j['attempts'][-1]['message']['content'])
        required = {'choice', 'mentioned_A', 'mentioned_B', 'rationale'}
        if set(raw) != required or raw['choice'] not in LABELS or not isinstance(raw['rationale'], str):
            raise ValueError(f'Invalid final JSON schema {key}')
        if any(type(raw[f'mentioned_{sense}']) is not bool for sense in 'AB'):
            raise ValueError(f'Invalid mention flag {key}')
        remap = {'A': 'A', 'B': 'B'} if j['order'] == 'AB' else {'A': 'B', 'B': 'A'}
        normalized = remap.get(raw['choice'], raw['choice'])
        mentioned = {remap[s]: raw[f'mentioned_{s}'] for s in 'AB'}
        if normalized != j['selected'] or mentioned != j['mentioned']:
            raise ValueError(f'Incorrect normalized fields {key}')
        if normalized in 'AB' and not mentioned[normalized]:
            flags.append({'id': j['id'], 'order': j['order'], 'issue': 'selected sense is marked unmentioned'})
        if normalized == 'no_answer' and any(mentioned.values()):
            flags.append({'id': j['id'], 'order': j['order'], 'issue': 'no_answer with a mentioned reference meaning'})
        by_key[key] = j
        all_ids.add(j['id'])
        attempts += len(j['attempts'])
    if set(by_key) != {(i, o) for i in all_ids for o in ('AB', 'BA')}:
        raise ValueError('Missing reference orders')
    if {m['id'] for m in mapping} != all_ids:
        raise ValueError('Mapping does not cover exactly the judged answers')
    rows, seen_rows, seen_targets = [], set(), set()
    for m in mapping:
        row, item, target = m['row'], m['id'], m['target']
        if row in seen_rows or (item, target) in seen_targets or target not in ('A', 'B'):
            raise ValueError('Duplicate/invalid score mapping')
        seen_rows.add(row)
        seen_targets.add((item, target))
        human = human_by_row.get(row)
        if human is not None and (type(human) is not int or human not in (0, 1)):
            raise ValueError('Invalid human label')
        ab, ba = [int(by_key[(item, order)]['selected'] == target) for order in ('AB', 'BA')]
        rows.append({**m, 'human': human, 'AB': ab, 'BA': ba, 'orders_agree': ab == ba})
    if set(human_by_row) - seen_rows:
        raise ValueError('Human labels contain unknown score rows')
    available = [r for r in rows if r['human'] is not None]
    consistent = [r for r in available if r['orders_agree']]
    ab_result = agreement([r['human'] for r in available], [r['AB'] for r in available])
    ba_result = agreement([r['human'] for r in available], [r['BA'] for r in available])
    subset = agreement([r['human'] for r in consistent], [r['AB'] for r in consistent])
    flipped = [i for i in sorted(all_ids) if by_key[(i, 'AB')]['selected'] != by_key[(i, 'BA')]['selected']]
    n_consistent = sum(r['orders_agree'] for r in rows)
    all_rows = len(available) == len(rows)
    if n_consistent / len(rows) < .90:
        gate_status = 'fails_order_threshold'
    elif not all_rows:
        gate_status = 'pending_human_labels'
    elif len(consistent) != len(rows):
        gate_status = 'pending_order_conflict_policy'
    elif subset['kappa'] is None:
        gate_status = 'kappa_undefined'
    elif subset['kappa'] < .6:
        gate_status = 'fails_kappa_threshold'
    else:
        gate_status = 'numeric_thresholds_met_semantic_review_required'
    return {
        'logical_judgments': len(judgments), 'actual_attempts': attempts,
        'first_attempt_valid': sum(len(j['attempts']) == 1 for j in judgments),
        'answers': len(all_ids), 'target_score_rows': len(rows),
        'target_order_consistent': n_consistent,
        'target_order_disagreements': len(rows) - n_consistent,
        'target_order_agreement': n_consistent / len(rows),
        'order_threshold_90_percent_met': n_consistent / len(rows) >= .90,
        'choice_order_disagreement_ids': flipped,
        'choice_order_agreement': (len(all_ids) - len(flipped)) / len(all_ids),
        'human_labels_available': len(available),
        'pending_human_adjudications': [{'id': r['id'], 'row': r['row'], 'target': r['target']} for r in rows if r['human'] is None],
        'ab_vs_available_human': ab_result, 'ba_vs_available_human': ba_result,
        'order_consistent_subset_vs_human': subset,
        'all_rows_kappa_ab': ab_result['kappa'] if all_rows else None,
        'all_rows_kappa_ba': ba_result['kappa'] if all_rows else None,
        'pilot_gate_status': gate_status,
        'acceptance_note': 'Team thresholds are >=90% target-order agreement and kappa>=0.6 across all rows. When orders conflict, the team must specify the final-label comparison policy. No conflicting rows are silently removed for acceptance. Structural flags are reported separately; this audit does not validate judge semantics.',
        'structural_coherence_flags': flags, 'rows': rows,
    }


def workbook_humans(path, mapping):
    wb = openpyxl.load_workbook(path, data_only=False, read_only=True)
    try:
        expected = set()
        for row in range(2, wb['Agreement'].max_row + 1):
            if not re.fullmatch(r'J\d+', str(wb['Agreement'].cell(row, 1).value or '')):
                continue
            ref = re.fullmatch(r'=Review!A(\d+)', str(wb['Agreement'].cell(row, 2).value or ''))
            if not ref:
                raise ValueError(f'Invalid complete workbook mapping at row {row}')
            item = wb['Review'].cell(int(ref[1]), 1).value
            target = wb['Agreement'].cell(row, 5).value
            expected.add((row, item, target))
        supplied = {(m['row'], m['id'], m['target']) for m in mapping}
        if not expected or supplied != expected or len(supplied) != len(mapping):
            raise ValueError('Supplied mapping does not match the complete workbook judgment rows')
        human, xinyi_labels, kc_labels = {}, [], []
        for m in mapping:
            row, target = m['row'], m['target']
            reference = wb['Agreement'].cell(row, 2).value
            match = re.fullmatch(r'=Review!A(\d+)', reference or '')
            if not match:
                raise ValueError(f'Unknown Review join at Agreement!B{row}')
            review_row = int(match[1])
            if wb['Review'].cell(review_row, 1).value != m['id'] or wb['Agreement'].cell(row, 5).value != target:
                raise ValueError(f'Workbook/mapping identity mismatch at row {row}')
            xinyi = wb['Agreement'].cell(row, 6).value
            label = wb['Review'].cell(review_row, 7).value
            allowed = {'Meaning A', 'Meaning B', "Lists several, doesn't pick", 'Picks a different meaning', 'Asks a question / no answer'}
            if label not in allowed or type(xinyi) is not int or xinyi not in (0, 1):
                raise ValueError(f'Unexpected human source label at row {row}: {label!r}')
            kc = int(label == f'Meaning {target}')
            adjudicated = wb['Qwen_check'].cell(row, 8).value
            if adjudicated not in (None, '') and (type(adjudicated) is not int or adjudicated not in (0, 1)):
                raise ValueError(f'Invalid adjudication H{row}')
            human[row] = xinyi if xinyi == kc else (adjudicated if adjudicated != '' else None)
            xinyi_labels.append(xinyi)
            kc_labels.append(kc)
        return human, agreement(xinyi_labels, kc_labels)
    finally:
        wb.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, default=ROOT / 'inputs/Qwen_pilot_run_records.zip')
    parser.add_argument('--workbook', type=Path, default=ROOT / 'inputs/Tokenify_blind_review_SIMPLE.xlsx')
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'outputs/judge_audit')
    args = parser.parse_args()
    with zipfile.ZipFile(args.archive) as z:
        records = json.loads(z.read('results.json'))
    humans, original = workbook_humans(args.workbook, records['mapping'])
    result = audit_records(records, humans)
    result['original_reviewers'] = original
    result['run'] = 'Archived November 1 run; not the fixed-prompt rerun'
    result['model'] = records['manifest']['model']
    result['source_sha256'] = {str(p.resolve().relative_to(ROOT)) if p.resolve().is_relative_to(ROOT) else str(p.resolve()): hashlib.sha256(p.read_bytes()).hexdigest() for p in (args.archive, args.workbook)}
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / 'audit.json').write_text(json.dumps(result, indent=2) + '\n')
    with (args.output_dir / 'target_scores.csv').open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(result['rows'][0]))
        writer.writeheader()
        writer.writerows(result['rows'])
    print(json.dumps({k: v for k, v in result.items() if k not in ('rows', 'source_sha256')}, indent=2))


if __name__ == '__main__':
    main()
