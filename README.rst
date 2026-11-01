Tokenify pilot
==============

Portable code and sanitized artifacts from the October 2026 pilot on field cues
and word-sense selection. Python 3.10+; standard library only.

This is the original pilot baseline, packaged for handoff on November 1, 2026.
The generation and validation logic is extracted from the original project;
run.py is a new portable command-line wrapper. It does not silently fix the
original judge's format failures. Qwen judging, Gemini/OLMo generation, revised
implicit cues, and the new evaluation rubric are follow-up work.

Included
--------

* 45 historical request/response pairs: 9 terms, C0 + two C1 + two C2 per term.
* Two reference definitions and source URLs per term, with stable sense IDs.
* 90 historical Claude evaluation texts, including all 16 invalid records.
* Original evaluator instructions and validator.
* Original C2 cues and ExpertQA selection indices/checksum.
* Offline tests and an audit that reproduces the formatting failure counts.

The 10th candidate, sensitivity, was not included in generation. These files
do not include human-review comments, credentials, private documents, full
provider envelopes, or encrypted reasoning fields.

Quick start (offline; no API calls)
----------------------------------

From the repository directory::

    python3 -m unittest discover -s tests -v
    python3 run.py audit
    python3 run.py export-judge --out runs/judge_jobs.jsonl

Expected audit: 45 requests, 45 responses, 90 evaluations; 74 valid and 16 invalid.
Invalid records: 5 leading-space rationale keys, 6 missing-field records, and
5 invalid JSON strings. The source texts and original statuses are unchanged.
A formatting failure is not converted into a score of zero.

The exported jobs hide generator identity and previous judgments. Each response
has AB and BA jobs containing identical references in reversed order.

Generation
----------

Set OPENAI_API_KEY in your local environment. Never commit it. Use an exact model
ID available to your account. Historical generator ID: gpt-5.6-sol. Historical
max_output_tokens: 4096; other sampling settings were provider defaults.

Preview without sending requests::

    python3 run.py generate --model YOUR_MODEL_ID --out runs/new_responses.jsonl

Execute one request::

    python3 run.py generate --model YOUR_MODEL_ID --out runs/new_responses.jsonl --limit 1 --execute

Use --limit 45 for the whole pilot. Successful and incomplete responses are skipped
on resume. Use a fresh output path when changing the model, requests or configuration.
Technical failures stop the run; rerun explicitly after resolving the error.
Outputs record IDs, model, configuration, status and response text.

Historical Claude judge
-----------------------

Set ANTHROPIC_API_KEY locally. Historical judge ID: claude-sonnet-5-5.
Historical max_tokens: 2048. First export jobs, then preview::

    python3 run.py judge-legacy --jobs runs/judge_jobs.jsonl --model YOUR_CLAUDE_MODEL_ID --out runs/judgments.jsonl

Add --execute --limit 1 for one paid request, or --limit 90 for the full set.
This baseline deliberately retains strict historical validation and no automatic
format repair or retry. Incomplete and invalid outputs are preserved. Do not use
python -O because the preserved validator uses assertions.

Qwen/Gemini/OLMo handoff
----------------------

The next model adapter should accept the saved role/content message list and
return text, status, exact model identifier and settings. C2 includes a fixed
assistant message, "Understood.", supplied as part of one API request rather
than generated in a separate turn.

For Qwen, reuse the exported judge payloads and version any rubric/schema changes.
Do not overwrite historical evaluations. Confirm the exact Hugging Face repository,
revision and parameter count before citing the model; "Qwen 3.8" is not a verified
identifier in this release. The newer cues have not been finalized in this archive.

ExpertQA pilot
--------------

The pilot selected 9 questions, producing 30 requests: 6 questions under C0-C3
and 3 unrelated questions under C0/C3. The original dataset is not redistributed.
Obtain r2_compiled_anon.jsonl from the authors' repository and retain its provenance:

https://github.com/chaitanyamalaviya/ExpertQA

Then prepare the historical messages::

    python3 run.py prepare-expertqa --source /path/to/r2_compiled_anon.jsonl --out runs/expertqa_requests.jsonl

The source checksum must match data/expertqa_selection.json; this prevents applying
row indices to a changed dataset. This command recreates messages, not original
request IDs or gold answers. Historically the reference concatenated revised_claim
values from the first lexicographically sorted annotated answer system.
ExpertQA content scoring is not implemented in this baseline.

Interpretation and provenance
-----------------------------

Selection earns 1 when the intended reference sense is selected, its essential
meaning is preserved, and its core meaning is not contradicted. Mere mention
without selection earns 0. The original rubric also records mention separately.
C0 has 9 responses but 18 reference-target judgments.

Request/response IDs in the published pilot artifacts preserve their original
values for joins. They identify the original records and are not hashes of the
reduced public records. Existing human decisions are kept outside this repository.
The historical automatic judgments have not been independently validated here.

Source-based definitions are short project paraphrases; cited sources retain
their rights. ExpertQA redistribution and use remain subject to its source terms.
No third-party dataset license is replaced by this repository.

Code and documentation were prepared with AI coding assistance and human project
direction. Offline tests validate packaging and protocol behavior, not research
conclusions. No live calls are made by the tests or CI.
