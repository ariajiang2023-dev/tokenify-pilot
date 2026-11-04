"""Read-only pilot statistics. Outputs describe the workbook transcription."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from functools import lru_cache
import hashlib
import html
import importlib.metadata
import json
from pathlib import Path
import platform
import re
import statistics
import unicodedata

import openpyxl


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ANALYSIS = ROOT / "inputs" / "Tokenify_analysis_plan.xlsx"
DEFAULT_BLIND = ROOT / "inputs" / "Tokenify_blind_review_SIMPLE.xlsx"
SUPPORT = Path(__file__).resolve().parent / "_support"
PINNED_FK_DEPENDENCIES = {"textstat": "0.7.13", "nltk": "3.10.4", "pyphen": "0.18.1"}
CMUDICT_SHA256 = "cad209c39eb87677d64e93d97f8eed10b7e6f9bdd42de8e7ca8efc8e17d62e8a"
CMUDICT_COMMIT = "5f09e470fe339e1a119dd174ca284129a3c7a5bb"
FIELD_ALIASES = {
    "computer science": "CS", "cs": "CS",
    "education/psychology": "EDPSY", "education and psychology": "EDPSY",
    "ed/psych": "EDPSY", "edpsy": "EDPSY",
    "medicine/healthcare": "HLTH", "health": "HLTH", "hlth": "HLTH",
    "stats": "STAT", "statistics": "STAT", "stat": "STAT", "control": "CONTROL",
}
PILOT_IMPLICIT_FIELDS = {
    "patient assessment and clinical care": "HLTH",
    "human development and learning in classrooms": "EDPSY",
    "operating systems and software engineering": "CS",
    "probability theory and statistical inference": "STAT",
}
METHODOLOGY = {
    "metric_source": "Workbook transcription, not verified raw generation output. Numeric content is retained.",
    "word_tokenization": (
        "NFC-normalized maximal runs of Unicode letters/numbers and following combining marks; "
        "internal straight/curly apostrophes and ASCII/U+2010/U+2011 hyphens join a token only "
        "between letters/numbers. Underscores and other punctuation separate tokens. "
        "A contiguous CJK run is one token; this is not linguistic segmentation or model token count."
    ),
    "plain_text": (
        "Decode literal backslash-n/r/t whitespace. Retain link labels and image alt text, remove "
        "link URLs, HTML tags, Markdown heading/list/quote/fence/emphasis markers, and table separator "
        "rows. Code content and inline code text remain. Remove paired TeX \\[...\\], \\(...\\), "
        "$$...$$ and $...$ math spans. Other words and numbers remain, including suspected page numbers. "
        "This is a documented lightweight transformation, not a complete Markdown parser."
    ),
    "sentence_units": (
        "Split transformed text at newlines or runs of .!? followed by whitespace/end; count segments "
        "containing at least one word. Headings/list fragments therefore count as units. "
        "Abbreviations, code and layout can distort this approximation."
    ),
    "reading_grade_ari": (
        "Automated Readability Index = 4.71 * alphanumeric_characters / words + "
        "0.5 * words / sentence_units - 21.43. Characters are Unicode letters/numbers "
        "inside counted tokens (excluding apostrophes/hyphens/combining marks). "
        "Values are unrounded and unclamped; summary CSVs retain full numeric precision. "
        "English-oriented rough US grade estimate only: not validated comprehension, answer quality, "
        "appropriateness, or a human reading-level assessment. Technical notation and short answers "
        "can produce misleading or negative grades. ARI is a secondary descriptive metric."
    ),
    "reading_grade_fk": (
        "Prespecified Flesch-Kincaid grade from textstat==0.7.13, language en_US, rounding disabled, "
        "on the same plain_text_for_metrics. Formula: 0.39 * FK_words / FK_sentences + "
        "11.8 * FK_syllables / FK_words - 15.59. The separately exported fk_n_* components use "
        "textstat's own counting rules, not n_words or n_sentence_units: punctuation is removed, "
        "tokens are split on whitespace, ASCII hyphens/contractions are retained, sentence matching "
        "ignores fragments of <=2 words and has a minimum of one for nonempty text. "
        "Syllables use the first CMUdict pronunciation; missing words use "
        "len(Pyphen(en_US).positions(word)) + 1. CMUdict and dependency hashes/versions are recorded. "
        "This English-oriented estimate is not a validated measure of comprehension or answer quality. "
        "Technical notation, code, transcription artifacts and short answers can distort it. "
        "Values remain unrounded and unclamped; negative scores are retained."
    ),
    "missing_values": "Empty/missing text or zero prose words => unavailable metrics (JSON null / empty CSV cell), not zero.",
    "dependencies": "openpyxl for read-only input; pinned textstat/nltk/pyphen and local CMUdict for FK; Python standard library for Unicode word counts and ARI. No runtime downloads.",
    "expertqa_join": (
        "Full question comes from the recorded C0 prompt, matched to ExpertQA_Sample after NFKC, "
        "case-folding, whitespace and punctuation normalization. No fuzzy or semantic ID matching. "
        "Unmatched indices remain null; strict mode rejects them. Question-column fragments and raw prompts remain preserved."
    ),
    "official_reference_mapping_note": (
        "The separate official ExpertQA reference audit recovered the miner question at zero-based "
        "index 282 and music question at index 509. These are absent from this workbook's selected "
        "ExpertQA_Sample pool. The eqa_index/source_join_status fields here describe joins to that "
        "workbook pool only; their nulls do not mean the official references are unavailable."
    ),
}


def unicode_words(text: str) -> list[str]:
    """Return deterministic Unicode tokens under the rule in METHODOLOGY."""
    text = unicodedata.normalize("NFC", text)
    tokens, current = [], []
    for pos, char in enumerate(text):
        category = unicodedata.category(char)[0]
        connector = char in "'’‐‑-" and current and pos + 1 < len(text) and text[pos + 1].isalnum()
        if category in {"L", "N"} or (category == "M" and current) or connector:
            current.append(char)
        elif current:
            tokens.append("".join(current))
            current = []
    if current:
        tokens.append("".join(current))
    return tokens


def plain_text(raw: str | None) -> str:
    """Make the explicitly documented analysis text; never alter the raw cell."""
    if raw is None:
        return ""
    text = str(raw).replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\r", "\n").replace("\\t", "\t")
    text = re.sub(r"\\\[.*?\\\]|\\\(.*?\\\)|\$\$.*?\$\$|(?<!\$)\$[^$\n]+\$(?!\$)", " ", text, flags=re.S)
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = html.unescape(re.sub(r"<[^>]*>", " ", text))
    text = re.sub(r"(?m)^\s*(?:```|~~~)[^\n]*$", "", text)
    text = re.sub(r"(?m)^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$", "", text)
    text = re.sub(r"(?m)^\s*(?:#{1,6}\s+|>\s*|[-+*]\s+|\d+[.)]\s+)", "", text)
    return text.translate(str.maketrans({"*": "", "`": "", "_": " ", "|": " "})).strip()


@lru_cache(maxsize=1)
def _fk_backend():
    """Require pinned local resources before textstat can attempt a download."""
    try:
        versions = {name: importlib.metadata.version(name) for name in PINNED_FK_DEPENDENCIES}
    except importlib.metadata.PackageNotFoundError as error:
        raise RuntimeError("FK dependencies missing; create .venv and install requirements.txt") from error
    if versions != PINNED_FK_DEPENDENCIES:
        raise RuntimeError(f"FK dependency versions differ from the pinned environment: {versions}")
    dictionary = SUPPORT / "nltk_data" / "corpora" / "cmudict" / "cmudict"
    if not dictionary.is_file() or hashlib.sha256(dictionary.read_bytes()).hexdigest() != CMUDICT_SHA256:
        raise RuntimeError("Pinned local CMUdict missing or changed; run python scripts/prepare_resources.py before computing FK")
    import nltk
    from textstat.textstat import textstatistics
    # A task-local, checksum-checked corpus takes precedence over global NLTK data.
    local_data = str(SUPPORT / "nltk_data")
    if local_data not in nltk.data.path:
        nltk.data.path.insert(0, local_data)
    reader = textstatistics()
    reader.set_lang("en_US")
    reader.set_rounding_points(None)
    return reader, nltk.corpus.cmudict.dict()


def _fk_measure(text: str) -> dict:
    reader, dictionary = _fk_backend()
    from textstat.backend.selections import list_words
    words = list_words(text, lowercase=True)
    cmu_words = sum(word in dictionary and bool(dictionary[word]) for word in words)
    return {
        "reading_grade_fk": reader.flesch_kincaid_grade(text),
        "fk_n_words": reader.lexicon_count(text),
        "fk_n_sentences": reader.sentence_count(text),
        "fk_n_syllables": reader.syllable_count(text),
        "fk_n_cmudict_words": cmu_words,
        "fk_n_pyphen_fallback_words": len(words) - cmu_words,
    }


def _fk_runtime_metadata() -> dict:
    _fk_backend()
    import pyphen
    dictionary = Path(pyphen.LANGUAGES["en_US"])
    return {
        "dependencies": {name: importlib.metadata.version(name) for name in (*PINNED_FK_DEPENDENCIES, "openpyxl")},
        "fk_language": "en_US", "fk_rounding": None,
        "cmudict": {
            "relative_path": "_support/nltk_data/corpora/cmudict/cmudict",
            "dictionary_sha256": CMUDICT_SHA256,
            "upstream_commit": CMUDICT_COMMIT,
            "source_url": f"https://raw.githubusercontent.com/nltk/nltk_data/{CMUDICT_COMMIT}/packages/corpora/cmudict.zip",
            "archive_sha256": "d07cca47fd72ad32ea9d8ad1219f85301eeaf4568f8b6b73747506a71fb5afd6",
        },
        "pyphen_dictionary": {"filename": dictionary.name, "sha256": hashlib.sha256(dictionary.read_bytes()).hexdigest()},
    }


def measure_text(raw: str | None) -> dict:
    text = plain_text(raw)
    words = unicode_words(text)
    result = {
        "plain_text_for_metrics": text,
        "n_words": None,
        "n_alphanumeric_characters": None,
        "n_sentence_units": None,
        "reading_grade_ari": None,
        "reading_grade_fk": None,
        "fk_n_words": None,
        "fk_n_sentences": None,
        "fk_n_syllables": None,
        "fk_n_cmudict_words": None,
        "fk_n_pyphen_fallback_words": None,
        "metric_status": "unavailable",
    }
    if not words:
        return result
    characters = sum(c.isalnum() for word in words for c in word)
    sentences = sum(bool(unicode_words(part)) for part in re.split(r"[.!?]+(?=\s|$)|\n+", text))
    result.update(
        n_words=len(words), n_alphanumeric_characters=characters,
        n_sentence_units=sentences,
        reading_grade_ari=4.71 * characters / len(words) + 0.5 * len(words) / sentences - 21.43,
        metric_status="available_workbook_transcription",
    )
    result.update(_fk_measure(text))
    return result


def _table(path: Path, sheet: str) -> list[dict]:
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        rows = iter(wb[sheet].iter_rows(values_only=True))
        headers = next(rows)
        return [
            {**{str(h): row[i] if i < len(row) else None for i, h in enumerate(headers) if h is not None}, "_source_row": number}
            for number, row in enumerate(rows, start=2) if any(v is not None for v in row)
        ]
    finally:
        wb.close()


def load_sources(analysis_plan: Path = DEFAULT_ANALYSIS, blind_review: Path = DEFAULT_BLIND) -> dict:
    """Load relevant worksheet values read-only; do not save either workbook."""
    return {
        **{s: _table(Path(analysis_plan), s) for s in ("Terms", "Contexts", "ExpertQA_Sample")},
        **{s: _table(Path(blind_review), s) for s in ("Review", "Agreement", "ExpertQA_scoring")},
    }


def _key(text) -> str:
    text = unicodedata.normalize("NFKC", str(text or "")).casefold()
    return " ".join("".join(c if c.isalnum() else " " for c in text).split())


def _field(value) -> str:
    return FIELD_ALIASES.get(str(value or "").strip().casefold(), str(value or "UNKNOWN"))


def _unique(rows, key, label):
    index = {}
    for row in rows:
        identifier = row[key]
        if identifier in index:
            raise ValueError(f"duplicate {label}: {identifier}")
        index[identifier] = row
    return index


def _cue_field(prompt: str) -> str | None:
    match = re.search(r"My academic field is ([^.]+)\.", prompt)
    if match:
        return _field(match.group(1))
    for cue, field in PILOT_IMPLICIT_FIELDS.items():
        if cue in prompt:
            return field
    return None


def extract_responses(tables: dict, require_complete_eqa_join: bool = False) -> list[dict]:
    """Keep all responses; reject duplicate/conflicting or structurally incomplete joins."""
    review = [r for r in tables["Review"] if re.fullmatch(r"S\d+", str(r.get("#", "")))]
    eqa = [r for r in tables["ExpertQA_scoring"] if re.fullmatch(r"E\d+", str(r.get("#", "")))]
    _unique(review + eqa, "#", "response ID")
    agreement = [r for r in tables["Agreement"] if re.fullmatch(r"J\d+", str(r.get("judgment", "")))]
    _unique(agreement, "judgment", "Agreement judgment ID")
    joined = defaultdict(list)
    for row in agreement:
        joined[row["row #"]].append(row)
    if set(joined) != {r["#"] for r in review}:
        raise ValueError("incomplete Review/Agreement response-ID join")
    responses = []
    for row in review:
        matches = joined[row["#"]]
        conditions = {r["condition"] for r in matches}
        if len(conditions) != 1 or any(r["term"] != row["Term"] for r in matches):
            raise ValueError(f"conflicting Agreement condition/term for {row['#']}")
        condition = next(iter(conditions))
        expected = {"A", "B"} if condition == "C0" else {row["target"]}
        if condition not in {"C0", "C1", "C2"} or {r["target"] for r in matches} != expected or len(matches) != len(expected):
            raise ValueError(f"incomplete Review/Agreement target join for {row['#']}")
        field_a = str(row["Meaning A"]).split(":", 1)[0]
        field_b = str(row["Meaning B"]).split(":", 1)[0]
        cue = None if condition == "C0" else _field(field_a if row["target"] == "A" else field_b)
        responses.append({
            "response_id": row["#"], "dataset": "Review", "source_sheet_row": row["_source_row"],
            "term": row["Term"], "question_raw": None, "question": None,
            "question_status": "not_recorded_in_review", "condition": condition,
            "field": cue or "UNCUED", "field_source": "recorded_reference_meaning_and_target",
            "cue_field": cue, "meaning_a_raw": row["Meaning A"], "meaning_b_raw": row["Meaning B"],
            "field_a": _field(field_a), "field_b": _field(field_b), "target": row["target"],
            "prompt_raw": row["What the student told the model"], "eqa_index": None,
            "response_text_raw": row["Model's answer"], "source_join_status": "complete_agreement_join",
            "quality_flags": ["workbook_transcription", "raw_generation_unverified", "standalone_question_not_recorded"],
        })

    sample = [r for r in tables["ExpertQA_Sample"] if isinstance(r.get("eqa_index"), (int, float))]
    _unique(sample, "eqa_index", "ExpertQA source ID")
    by_question = defaultdict(list)
    for row in sample:
        by_question[_key(row["question"])].append(row)
    groups = []
    for row in eqa:
        if row["condition"] == "C0":
            groups.append([])
        if not groups:
            raise ValueError("incomplete ExpertQA C0 condition join")
        groups[-1].append(row)
    for group in groups:
        baseline = group[0]
        question = str(baseline["what was sent"]).rsplit("user:", 1)[-1].strip()
        question = " ".join(question.split())
        matches = by_question.get(_key(question), [])
        if len(matches) > 1:
            raise ValueError(f"ambiguous ExpertQA source question: {baseline['#']}")
        source = matches[0] if matches else None
        conditions = [r["condition"] for r in group]
        if len(conditions) != len(set(conditions)) or any(c not in {"C0", "C1", "C2", "C3"} for c in conditions):
            raise ValueError(f"duplicate/invalid ExpertQA condition in group {baseline['#']}")
        recorded_field = next((_cue_field(r["what was sent"]) for r in group if r["condition"] == "C1"), None)
        for row in group:
            if not _key(row["question"]) or not _key(question).endswith(_key(row["question"])) or _key(question) not in _key(row["what was sent"]):
                raise ValueError(f"ExpertQA question mismatch within C0 group: {row['#']}")
            flags = ["workbook_transcription", "raw_generation_unverified"]
            if _key(row["question"]) != _key(question):
                flags.append("question_column_truncated")
            if row["#"] in {"E12", "E18"}:
                flags.append("known_page_number_artifact_in_response")
            if row["#"] in {"E07", "E18", "E23"}:
                flags.append("known_page_number_artifact_in_prompt")
            if source is None:
                flags.append("unmatched_source_question")
            responses.append({
                "response_id": row["#"], "dataset": "ExpertQA_scoring", "source_sheet_row": row["_source_row"],
                "term": None, "question_raw": row["question"], "question": question,
                "question_status": "full_question_from_recorded_C0_prompt", "condition": row["condition"],
                "field": _field(source["mapped_discipline"]) if source else recorded_field or "UNKNOWN",
                "field_source": "ExpertQA_Sample" if source else "recorded_C1_cue_not_source_id" if recorded_field else "unavailable",
                "cue_field": _cue_field(row["what was sent"]),
                "meaning_a_raw": None, "meaning_b_raw": None, "field_a": None, "field_b": None, "target": None,
                "prompt_raw": row["what was sent"], "eqa_index": int(source["eqa_index"]) if source else None,
                "response_text_raw": row["model's answer"],
                "source_join_status": "complete_source_question_join" if source else "unmatched_source_question",
                "quality_flags": flags,
            })
    unresolved = [r["response_id"] for r in responses if r["source_join_status"] == "unmatched_source_question"]
    if require_complete_eqa_join and unresolved:
        raise ValueError("incomplete ExpertQA source-question join: " + ", ".join(unresolved))
    return responses


def build_inventory(tables: dict) -> dict:
    terms = [r for r in tables["Terms"] if re.fullmatch(r"T\d+", str(r.get("term_id", "")))]
    contexts = [r for r in tables["Contexts"] if re.fullmatch(r"T\d+", str(r.get("term_id", "")))]
    term_by_id = _unique(terms, "term_id", "term ID")
    context_by_id = _unique(contexts, "term_id", "context term ID")
    if set(term_by_id) != set(context_by_id):
        raise ValueError("incomplete Terms/Contexts term-ID join")
    for term_id, term in term_by_id.items():
        if (term["term"] or None) != (context_by_id[term_id]["term"] or None):
            raise ValueError(f"Terms/Contexts term mismatch: {term_id}")
    active = [r for r in terms if r["term"]]
    complete = [r for r in active if r["field A"] and r["field B"]]
    field_membership = Counter(_field(r[k]) for r in active for k in ("field A", "field B") if r[k])
    pairs = Counter(" + ".join(sorted((_field(r["field A"]), _field(r["field B"])))) for r in complete)
    sample = [r for r in tables["ExpertQA_Sample"] if isinstance(r.get("eqa_index"), (int, float))]
    _unique(sample, "eqa_index", "ExpertQA source ID")
    standalone = sum(bool(r["question (asked on its own)"]) for r in contexts)
    available_contexts = sum(bool(r[f"context {i}"]) for r in contexts for i in range(1, 6))
    context_fields = Counter()
    unassigned_items = 0
    for row in contexts:
        available = bool(row["question (asked on its own)"]) + sum(bool(row[f"context {i}"]) for i in range(1, 6))
        term = term_by_id[row["term_id"]]
        fields = {_field(term[k]) for k in ("field A", "field B") if term[k]}
        if not fields:
            unassigned_items += available
        for field in fields:
            context_fields[field] += available
    return {
        "terms": {
            "slots": len(terms), "active_terms": len(active), "blank_term_slots": len(terms) - len(active),
            "complete_field_pairs": len(complete), "active_terms_without_complete_field_pair": len(active) - len(complete),
            "by_field_membership": dict(sorted(field_membership.items())), "by_complete_field_pair": dict(sorted(pairs.items())),
            "verified_yes": sum(str(r["verified?"]).casefold() == "yes" for r in active),
        },
        "contexts": {
            "term_slots": len(contexts), "available_standalone_questions": standalone,
            "available_contexts": available_contexts, "available_questions_and_contexts": standalone + available_contexts,
            "available_items_by_field_membership_nonexclusive": dict(sorted(context_fields.items())),
            "available_items_without_field_assignment": unassigned_items,
            "both_screeners_recorded": sum(bool(r["screened by (1)"]) and bool(r["screened by (2)"]) for r in contexts),
            "note": "Field membership counts overlap across a term's two meanings. Availability counts are not screening/approval counts; planned final target is 60 terms x 6 items.",
        },
        "expertqa_sample": {
            "candidate_items": len(sample),
            "by_mapped_discipline": dict(sorted(Counter(r["mapped_discipline"] or "UNKNOWN" for r in sample).items())),
            "by_field_raw": dict(sorted(Counter(r["field"] or "UNKNOWN" for r in sample).items())),
            "by_include": dict(sorted(Counter(r["include"] or "UNSET" for r in sample).items())),
            "approved_items": sum(str(r["include"]).casefold() == "yes" for r in sample),
            "note": "Suggested and TBD are not approved; final target of 420 is a plan, not a completed sample.",
        },
    }


def summarize(rows: list[dict]) -> list[dict]:
    groups = defaultdict(list)
    for row in rows:
        groups[("dataset", row["dataset"], "", "")].append(row)
        groups[("condition", row["dataset"], row["condition"], "")].append(row)
        groups[("field", row["dataset"], "", row["field"])].append(row)
    result = []
    for (kind, dataset, condition, field), members in sorted(groups.items()):
        words = [r["n_words"] for r in members if r["n_words"] is not None]
        grades = [r["reading_grade_ari"] for r in members if r["reading_grade_ari"] is not None]
        fk_grades = [r["reading_grade_fk"] for r in members if r["reading_grade_fk"] is not None]
        result.append({
            "group_type": kind, "dataset": dataset, "condition": condition, "field": field,
            "n_responses": len(members), "n_available_word_counts": len(words), "n_missing_word_counts": len(members) - len(words),
            "total_words": sum(words) if words else None, "mean_words": statistics.mean(words) if words else None,
            "median_words": statistics.median(words) if words else None,
            "sd_words": statistics.stdev(words) if len(words) >= 2 else None,
            "min_words": min(words) if words else None, "max_words": max(words) if words else None,
            "n_available_reading_grades": len(grades), "mean_reading_grade_ari": statistics.mean(grades) if grades else None,
            "n_available_reading_grades_fk": len(fk_grades), "mean_reading_grade_fk": statistics.mean(fk_grades) if fk_grades else None,
        })
    return result


def _write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        raise ValueError(f"refusing empty CSV without columns: {path}")
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def run_analysis(analysis_plan: Path, blind_review: Path, output_dir: Path, require_complete_eqa_join: bool = False) -> dict:
    """Create CSV/JSON artifacts after validation; originals remain read-only."""
    tables = load_sources(analysis_plan, blind_review)
    raw = extract_responses(tables, require_complete_eqa_join=require_complete_eqa_join)
    inventory = build_inventory(tables)
    measured = []
    for response in raw:
        identifiers = {k: v for k, v in response.items() if k not in {"response_text_raw", "prompt_raw", "meaning_a_raw", "meaning_b_raw"}}
        identifiers["quality_flags"] = ";".join(identifiers["quality_flags"])
        measured.append({**identifiers, **measure_text(response["response_text_raw"])})
    metadata = {
        "source_files": [{"path": str(Path(p).resolve().relative_to(ROOT)) if Path(p).resolve().is_relative_to(ROOT) else str(Path(p).resolve()), "sha256": hashlib.sha256(Path(p).read_bytes()).hexdigest()} for p in (analysis_plan, blind_review)],
        "runtime": {"python": platform.python_version(), "unicode_database": unicodedata.unidata_version, "openpyxl": openpyxl.__version__, **_fk_runtime_metadata()},
        "methodology": METHODOLOGY, "response_count": len(raw),
        "unresolved_expertqa_response_count": sum(r["source_join_status"] == "unmatched_source_question" for r in raw),
        "unresolved_expertqa_response_ids": [r["response_id"] for r in raw if r["source_join_status"] == "unmatched_source_question"],
        "source_text_status": "workbook_transcription_raw_generation_verification_pending",
    }
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, data in (("pilot_responses.json", raw), ("data_inventory.json", inventory), ("metrics_metadata.json", metadata)):
        (output_dir / name).write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    _write_csv(output_dir / "response_metrics.csv", measured)
    _write_csv(output_dir / "response_summary.csv", summarize(measured))
    return metadata


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--analysis-plan", type=Path, default=DEFAULT_ANALYSIS)
    parser.add_argument("--blind-review", type=Path, default=DEFAULT_BLIND)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "outputs" / "metrics")
    parser.add_argument("--require-complete-eqa-join", action="store_true", help="Reject missing source eqa_index mappings instead of exporting explicit nulls.")
    args = parser.parse_args(argv)
    try:
        result = run_analysis(args.analysis_plan, args.blind_review, args.output_dir, args.require_complete_eqa_join)
    except (ValueError, KeyError, RuntimeError) as error:
        parser.error(str(error))
    print(json.dumps({"response_count": result["response_count"], "unresolved_expertqa_response_count": result["unresolved_expertqa_response_count"], "output_dir": str(args.output_dir.resolve())}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
