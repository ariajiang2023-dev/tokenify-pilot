# Tokenify pilot

Code for the CS769 project **Discipline-Aware Adaptation in Language Models**.
Team: Kaycie Barron, James Fu, and Xinyi Jiang.

The repository contains the original October pilot runner and the November
workbook-analysis tools. Historical data and rubric text are preserved. Final
human labels remain in the team's Box workbook.

## Start here

Use Python 3.12 for the full toolset. From the repository root:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python scripts/prepare_resources.py
python -m unittest discover -s tests -v
python -m unittest tests.integration_resources -v
python -m tokenify_pilot audit
```

Resource setup downloads the pinned ExpertQA release and CMU pronunciation
dictionary from their official repositories and checks their hashes. Subsequent
analysis runs offline. Resource setup makes no model calls.
Portable tests need neither Box inputs nor downloaded caches. The separate
resource checks verify the actual dictionary measurements and ExpertQA matches.

The historical audit needs only Python's standard library (Python 3.10+):

```sh
python run.py audit
```

Expected historical audit: **45 requests, 45 responses, 90 Claude judgments;
74 valid and 16 invalid**. Invalid formatting remains invalid rather than
becoming a score of zero.

## Files

| Location | Contents |
|---|---|
| `tokenify_pilot/` | Historical generation, job export, and Claude validation |
| `tokenify_analysis/` | Workbook statistics, Qwen audit, and ExpertQA reference review |
| `data/pilot_v1/` | Original sanitized requests, responses, judgments, cues, and selection indices |
| `prompts/rubric_v1.txt` | Unchanged historical evaluator instructions |
| `scripts/prepare_resources.py` | Explicit download and verification of public analysis resources |
| `tests/` | Portable tests and a separate workbook-integration suite |
| `docs/historical_pilot.rst` | Historical generation settings, provider commands, and provenance |

`inputs/`, `outputs/`, `runs/`, downloaded caches, environments, and credentials
are excluded from Git. `FILE_SHA256.json` records hashes of the published files.

## Analyze the Box workbooks

Place current copies of these three files in a local `inputs/` directory:

- `Tokenify_analysis_plan.xlsx`
- `Tokenify_blind_review_SIMPLE.xlsx`
- `Qwen_pilot_run_records.zip` (from Box's `03_Qwen_judge_pilot` folder)

Then run:

```sh
python -m unittest tests.integration_workbooks -v
python -m tokenify_analysis.metrics
python -m tokenify_analysis.judge_audit
python -m tokenify_analysis.expertqa_reference_prep
```

Outputs go to `outputs/metrics`, `outputs/judge_audit`, and `outputs/expertqa`.
Each command supports `--help` for explicit input and output paths. Scripts read
the workbooks without changing them. The integration tests require the Box
inputs; the default portable suite does not.

The November 4 workbook snapshot contains **45 term answers and 30 ExpertQA
answers**. Statistics describe those workbook transcriptions. Suspected page
numbers and truncated question cells are retained and flagged. Flesch–Kincaid
grade uses textstat 0.7.13 in English without rounding; ARI is secondary.

The ExpertQA review matches nine questions to the pinned expert-revised answers,
retains released author IDs, and reads recorded labels and notes directly from
the workbook. Earlier pilot recommendations remain distinct from those labels.
The source-match guards reject changed prompts or responses rather than reusing
an old annotation.

The archived November 1 Qwen run has **43/54 target-order agreement (79.6%)**.
It fails the planned 90% threshold. Agreement on the filtered order-consistent
subset does not validate the judge. The fixed-prompt rerun remains a separate
task.

## Prepare full-run analysis

The RQ1–RQ3 analysis and independent-validation sampler are ready to consume
normalized full outputs. No full generation or new judge run is bundled here.
See [the input contract and commands](docs/full_run_analysis.md) before Xinyi
freezes the generation export. The contract preserves response IDs, term/context
identities, reference orders, ExpertQA author IDs, and first/repeated-run metadata.

```sh
python -m tokenify_analysis.full_run --input inputs/full_responses.jsonl \
  --judgments inputs/full_judgments.jsonl --compute-text-metrics \
  --output-dir outputs/full_analysis --bootstrap 10000 --seed 42
python -m tokenify_analysis.validation_sample --input inputs/full_responses.jsonl \
  --n 200 --seed 42 --output-dir outputs/validation_200
```

Check the pilot rerun without changing the workbook:

```sh
python -m tokenify_analysis.rerun_check \
  --workbook inputs/Tokenify_blind_review_SIMPLE.xlsx \
  --archive inputs/Qwen_pilot_run_records.zip --output-dir outputs/rerun_check
```

Optional `--run-id` and `--prompt-sha256` record operator-supplied metadata;
they do not verify the raw run. The package reports when current workbook
columns still match the archived run.

## Historical generation and judging

Export the original judge jobs without an API call:

```sh
python -m tokenify_pilot export-judge --out runs/judge_jobs.jsonl
```

Generation and legacy judging preview their configuration by default. Live calls
require `--execute` and an API key set in your local environment. Keep keys out
of files committed to Git. The original `python run.py ...` commands still work.
See [the historical pilot guide](docs/historical_pilot.rst) for provider commands
and archived settings. The historical cues and rubric are not the revised full-run
protocol.

## Independent validation

Keep the later 200-response validation separate from the pilot review. Hide
earlier drafts, judge labels, and judge rationales from both coders and the third
adjudicator. Freeze the development/held-out split before prompt tuning, and use
the held-out labels only for final evaluation.

ExpertQA and the pronunciation dictionary retain their source terms. Download
provenance and the dictionary's redistribution notice are kept under
`tokenify_analysis/_support`; this repository does not replace their licenses.
