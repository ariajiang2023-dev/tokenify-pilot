Full-run analysis contract
==========================

The full generation has not been run by these tools. The example under
`data/examples/` is entirely synthetic and is only a software check.
The planned primary run uses 60 approved nonpilot terms, each with a standalone
question and five contexts, plus 420 ExpertQA controls. Models are analyzed
separately. A later decision to drop a model does not require code changes.

Commands
--------

Run from the repository root after installing `requirements.txt` and preparing
the pinned public resources:

```sh
python -m tokenify_analysis.full_run \
  --input inputs/full_responses.jsonl \
  --judgments inputs/full_judgments.jsonl \
  --compute-text-metrics \
  --output-dir outputs/full_analysis --bootstrap 10000 --seed 42
```

The judgment file is optional if both normalized orders are already in the
response rows. The analysis writes CSV tables, coverage diagnostics, and source
hashes. It reads no labels from pilot drafts and changes no input files.
The word-count and Flesch–Kincaid step uses the same pinned implementation as
the pilot metrics. Omit `--compute-text-metrics` only when the supplied metric
columns have already been computed under that implementation; missing values
will remain missing.

Check the software without research inputs:

```sh
python -m tokenify_analysis.full_run \
  --input data/examples/full_run_synthetic.jsonl \
  --expected-contexts 1 --bootstrap 100 --output-dir outputs/synthetic_check
```

The synthetic example has one context per term. Do not use
`--expected-contexts 1` for the planned six-item study.

Response records
----------------

JSONL has one object per generated answer. CSV with the same names is also
accepted. Preserve these fields during generation and join annotations by
`response_id`, never by file order or an Excel row number.

| Field | Required contents |
|---|---|
| `response_id`, `request_id` | Distinct, stable string IDs; both must be unique across the file |
| `model` | Exact model/checkpoint identifier, including the Olmo stage |
| `set` | `terms` or `expertqa` |
| `item_id` | Stable item ID; one question/context of a term, or one ExpertQA question |
| `question` | Original question/context without the experimental cue; used for blinded coding |
| `response_text` | Complete, unchanged generated response |
| `split` | `main` or `pilot`; pilot rows are excluded from primary analyses |
| `response_phase` | `first` or `followup`; follow-ups are excluded from primary analyses |
| `repeat_index` | Integer 0 for the initial generation; 1–5 for the repeatability runs |
| `temperature` | Generation temperature; retain it for every run |
| `condition` | `C0`, `C1`, `C2`, `C3`, or pilot-only `C2-R` |
| `cue_field` | Null for C0; exact reference field label for term cues; selected field for controls |
| `data_origin` | Recommended provenance, for example `full_generation`; examples use `synthetic_example` |

The Coding sheet's existing headings for the cue, target, judge-order, ExpertQA,
and reading-grade columns are accepted as aliases. The sheet still needs the
additional metadata above. Exported formulas such as `orders agree (auto)` or
`meaning chosen (final)` are not trusted in place of the two original orders.

The public API `analyze` expects records already checked by `normalize_records`
or `load_records`; the CLI always performs that validation.

For **term rows**, also supply:

- `term_id`: same ID across all contexts, conditions, and models.
- `context_index`: 0 for the standalone question and 1–5 for the contexts.
- `field_a`, `field_b`, `reference_a`, `reference_b`: canonical reference identities
  and definitions. They must stay identical for a term across the complete file.
  Each term/context index must also retain the same item ID and uncued question
  across conditions, models, and repeats.
- `target`: `both` for the single C0 answer; `A` or `B` for a cued answer.
  Do not duplicate a C0 answer once per meaning. The analysis expands its two
  target scores without giving the term extra weight.
- `judge_ab`, `judge_ba`: independently normalized labels in
  `A`, `B`, `other`, `multiple`, `no_answer`, or null when unavailable.
- `mentioned_a_ab`, `mentioned_b_ab`, `mentioned_a_ba`, `mentioned_b_ba`:
  boolean mention flags for the canonical identities in each order, or null.
- `asked_clarification`: true/false if separately coded. A `no_answer` label
  without this flag remains `asks_or_no_answer` rather than becoming `asks`.
- `other_field`: optional recorded field for an `other` choice. Missing values
  remain `other_unspecified` rather than being inferred as a general meaning.

For **ExpertQA rows**, also supply:

- `author_id`: released question-author ID, preserved as a cluster identity.
- `target`: `NA`.
- `expected_cue_fields`: exact per-item, per-model cue allocation from the frozen
  generation manifest, for example `{"C1":["CS"],"C2":["CS"],"C3":["HLTH"]}`.
  Include this object on C0 and every cued row; a CSV cell contains its JSON text.
  Each condition needs a nonempty field list. Do not reconstruct the lists from
  the rows that happened to finish. Missing declared cues exclude the item from
  that condition's paired metrics, including when only C0 is present.
- `reference_answer`: verified expert answer, required before exporting coding
  sheets. Do not substitute a generated answer. A question must retain the
  same reference across models and conditions. All rows may lack a reference
  during preparation, but mixing missing and supplied references is rejected.
- `correct`: recorded 1/0 label or null.
- `change_vs_c0`: `none`, `examples or difficulty only`, `drops a key point`,
  or `contradicts a key point`, or null.
- `names_field`, `stereotyped`: true/false labels or null.
- `n_words`, `reading_grade_fk`: numeric values or null. The recommended command
  recomputes these from the raw response text.

The module does not score correctness, invent missing human labels, or infer
stereotypes. Supply those annotations from the validated coding pipeline.
Blank/unknown values do not become zeros. Duplicate response IDs, request IDs,
or design cells, inconsistent references, and missing structural IDs fail with
an error.

Joining judgment outputs
------------------------

A judgment JSONL can contain one row per response and order:

```json
{"response_id":"stable-id","order":"BA","choice":"B","mentioned_A":false,"mentioned_B":true}
```

Here A/B are the displayed reference positions; for BA this normalizes to
canonical meaning A. The join also accepts `selected` with an already-normalized
choice and `mentioned: {"A": true, "B": false}` in canonical identities.
Do not mix those two conventions in one record. A provider response naming
fields must first be mapped to the frozen term's canonical A/B identities.
The join rejects duplicate orders, unknown responses, invalid choices, failed
statuses, and overwriting existing judge labels. Missing orders stay unavailable.

Analysis rules
--------------

- **RQ1:** retain only complete, order-consistent, identical target/context
  coverage within each term and condition. A complete term has 12 target cells
  per condition: two targets times six items. Each term contributes one average.
  Report chose-target, mentioned-target, and listed-several separately. C1/C2
  versus C0 use two-sided paired t tests over terms; incomplete terms are listed
  as excluded for that comparison. Mention flags must agree across both orders.
  Exploratory field rates use a term-clustered percentile bootstrap.
- **RQ2:** match each cued control to its unique C0 answer by model and item ID.
  Check every condition against its declared cue fields, including wholly
  missing conditions. When a condition has multiple cue fields, average those cues equally within
  the item before forming its change. A missing metric in any paired cue makes
  that item's metric unavailable. Means give each item equal weight. Confidence
  intervals resample whole authors with all of their items, retaining unequal
  cluster sizes. Fewer than two authors produces no confidence interval.
- **RQ3:** use complete C0 term contexts and order-consistent labels. Compute
  behavior rates within term, then give terms equal weight. Confidence intervals
  resample terms. Default meaning shares retain canonical reference fields and
  recorded other-field labels.
- **Repeatability:** repeats, follow-ups, pilot data, and roommate diagnostics
  stay outside primary RQs. The separate variability table reports repeat
  counts, temperatures, normalized choice counts, and differences from the
  initial response. A five-run completeness flag is descriptive.

All missingness and exclusions appear in `coverage.json`. A successful command
is a software result, not proof that the judge is validated or the dataset is
complete. Compare coverage to the frozen generation manifest before writing
confirmatory results. These planned analyses do not establish causality or
validated comprehension from a reading-grade score.

Independent validation sample
-----------------------------

After full generation, freeze the sample before prompt tuning:

```sh
python -m tokenify_analysis.validation_sample \
  --input inputs/full_responses.jsonl --n 200 --seed 42 \
  --output-dir outputs/validation_200
```

Sampling is proportional across model × condition × dataset strata, with at
least one response per nonempty stratum. It is deterministic for a fixed input
and seed. The private key records each stratum's available and sampled counts.
Development and held-out assignments keep all selected responses from a term,
or from an ExpertQA author, in one split across models and conditions. Group
isolation takes priority over an exact 100/100 response count.

Distribute only the three CSVs under `blinded/` to the two coders and adjudicator.
They contain the question, references, response, and empty coding fields. Model,
condition, author, original response IDs, split membership, draft labels, judge
labels, and rationales are absent. Keep `private/` with the analyst. The command
refuses to overwrite a nonempty output directory. No coding decisions are
prefilled. Correctness validation requires independent human labels.

Numerical dependencies
----------------------

Paired t tests use SciPy's `ttest_rel`, pinned at 1.16.3 with NumPy 2.3.4.
Version availability and Python support were checked against the official
[PyPI SciPy metadata](https://pypi.org/pypi/scipy/1.16.3/json) and
[NumPy metadata](https://pypi.org/pypi/numpy/2.3.4/json).
Bootstrap intervals are two-sided 95% percentile intervals with a recorded seed.
