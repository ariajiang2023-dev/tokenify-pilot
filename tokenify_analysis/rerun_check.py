"""Read the 54 Qwen_check target scores without changing the workbook."""

import argparse
import csv
import hashlib
import json
from pathlib import Path
import re
import zipfile

import openpyxl

from .judge_audit import agreement, audit_records, workbook_humans


def check_identity(actual, expected, row):
    if tuple(actual) != tuple(expected):
        raise ValueError(f'Qwen_check identity mismatch at row {row}')


def assess_scores(rows, *, run_id=None, prompt_sha256=None):
    if len(rows) != 54:
        raise ValueError('The pilot check requires all 54 target-score rows')
    if bool(run_id) != bool(prompt_sha256):
        raise ValueError('Supply both run_id and prompt_sha256, or neither')
    if prompt_sha256 and not re.fullmatch(r'[0-9a-fA-F]{64}', prompt_sha256):
        raise ValueError('prompt_sha256 must be a 64-character SHA-256 digest')
    seen_judgments, seen_rows, seen_targets = set(), set(), set()
    for row in rows:
        if row['target'] not in ('A', 'B'):
            raise ValueError('Each score must target canonical meaning A or B')
        identity = (row['response_id'], row['target'])
        if row['judgment'] in seen_judgments or row['row'] in seen_rows or identity in seen_targets:
            raise ValueError('Duplicate judgment, row, or response/target identity')
        seen_judgments.add(row['judgment']); seen_rows.add(row['row']); seen_targets.add(identity)
        for field in ('human', 'AB', 'BA', 'archive_AB', 'archive_BA'):
            if type(row[field]) is not int or row[field] not in (0, 1):
                raise ValueError(f'Missing or invalid binary value: {field}, row {row["row"]}')
    matches = sum(row['AB'] == row['BA'] for row in rows)
    ab = agreement([r['human'] for r in rows], [r['AB'] for r in rows])
    ba = agreement([r['human'] for r in rows], [r['BA'] for r in rows])
    order_pass = matches >= 49
    kappa_pass = all(x['kappa'] is not None and x['kappa'] >= .6 for x in (ab, ba))
    if not order_pass:
        status = 'fails_order_threshold'
    elif not kappa_pass:
        status = 'fails_kappa_threshold'
    elif not run_id:
        status = 'rerun_evidence_missing'
    else:
        status = 'numeric_thresholds_met_review_raw_run'
    return dict(
        status=status, target_rows=54, human_rows=54, order_matches=matches,
        order_agreement=matches / 54, order_threshold_met=order_pass,
        all_rows_agreement_ab=ab['agreement'], all_rows_agreement_ba=ba['agreement'],
        all_rows_kappa_ab=ab['kappa'], all_rows_kappa_ba=ba['kappa'],
        conservative_all_row_kappa_threshold_met=kappa_pass,
        identical_to_archived_scores=all(r['AB'] == r['archive_AB'] and r['BA'] == r['archive_BA'] for r in rows),
        supplied_run_id=run_id, supplied_prompt_sha256=prompt_sha256,
        provenance_status='operator_supplied_metadata_not_raw_run_verification' if run_id else 'no_revised_run_metadata',
        policy='At least 49/54 target scores must agree across reference orders. Both all-row kappas must be >=0.6 for this conservative numerical check. No row is removed. Team must agree on the final policy for conflicting orders.',
        next_step='Verify raw outputs, revised prompt, model settings, response identities, and run provenance before declaring the revised judge ready.',
    )


def check_workbook(workbook, archive, *, run_id=None, prompt_sha256=None):
    with zipfile.ZipFile(archive) as z:
        records = json.loads(z.read('results.json'))
    mapping = records['mapping']
    humans, _ = workbook_humans(workbook, mapping)
    baseline = {r['row']: r for r in audit_records(records, humans)['rows']}
    wb = openpyxl.load_workbook(workbook, read_only=False, data_only=True)
    try:
        sheet = wb['Qwen_check']
        rows = []
        for item in mapping:
            row = item['row']
            check_identity((sheet.cell(row, 1).value, sheet.cell(row, 2).value, sheet.cell(row, 5).value),
                           (wb['Agreement'].cell(row, 1).value, item['id'], item['target']), row)
            if (sheet.cell(row, 14).value, sheet.cell(row, 15).value) != (baseline[row]['AB'], baseline[row]['BA']):
                raise ValueError(f'Preserved N/O scores differ from the raw November 1 archive at row {row}')
            rows.append(dict(row=row, judgment=sheet.cell(row, 1).value,
                             response_id=item['id'], target=item['target'], human=humans[row],
                             AB=sheet.cell(row, 10).value, BA=sheet.cell(row, 11).value,
                             archive_AB=sheet.cell(row, 14).value, archive_BA=sheet.cell(row, 15).value))
    finally:
        wb.close()
    result = assess_scores(rows, run_id=run_id, prompt_sha256=prompt_sha256)
    result['source_sha256'] = {Path(p).name: hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in (workbook, archive)}
    return result, rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workbook', type=Path, required=True)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--run-id')
    parser.add_argument('--prompt-sha256')
    args = parser.parse_args(argv)
    result, rows = check_workbook(args.workbook, args.archive, run_id=args.run_id, prompt_sha256=args.prompt_sha256)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / 'rerun_check.json').write_text(json.dumps(result, indent=2) + '\n')
    with (args.output_dir / 'rerun_target_scores.csv').open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
