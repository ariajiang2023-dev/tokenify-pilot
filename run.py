"""Portable CLI for the historical pilot. Live calls require --execute."""
import argparse
import collections
import json
import os
from pathlib import Path
import ssl
import time
import urllib.error
import urllib.request
from pilot_lib import read, write, digest, api_call, messages, seal
from legacy_validation import validate

ROOT = Path(__file__).resolve().parent
RUBRIC = (ROOT / "rubric_v1.txt").read_text()

def jobs(requests, responses):
    indexed = {r["request_id"]: r for r in requests}
    if len(indexed) != len(requests):
        raise ValueError("Duplicate request ID")
    seen = set()
    for r in responses:
        if r["status"] != "ok":
            continue
        if r["response_id"] in seen:
            raise ValueError("Duplicate response ID")
        seen.add(r["response_id"])
        q = indexed[r["request_id"]]
        for order in ("AB", "BA"):
            refs = q["references"] if order == "AB" else q["references"][::-1]
            payload = dict(question=q["question"], conversation=q["messages"],
                           answer=r["text"], reference_senses=[
                               {k: x[k] for k in ("sense_id", "field", "definition")} for x in refs])
            yield dict(job_id=digest([r["response_id"], order, RUBRIC, payload]),
                       response_id=r["response_id"], order=order, payload=payload)

def check(text, job):
    try:
        return "ok", validate(json.loads(text), job)
    except (ValueError, KeyError, AssertionError, TypeError):
        return "invalid_label", None

def audit():
    requests = read(ROOT / "data/pilot_requests.jsonl")
    responses = read(ROOT / "data/pilot_responses.jsonl")
    lookup = {(j["response_id"], j["order"]): j for j in jobs(requests, responses)}
    counts = collections.Counter()
    causes = collections.Counter()
    for r in read(ROOT / "data/legacy_judgments.jsonl"):
        status, _ = check(r["text"], lookup[(r["response_id"], r["order"])])
        if status != r["status"]:
            raise ValueError("Historical validation status changed")
        counts[status] += 1
        if status == "invalid_label":
            try:
                label = json.loads(r["text"])
            except ValueError:
                causes["invalid_json"] += 1
            else:
                if " rationale" in label:
                    causes["leading_space_in_rationale_key"] += 1
                else:
                    causes["missing_required_fields"] += 1
    return dict(requests=len(requests), responses=len(responses),
                judge_calls=sum(counts.values()), status_counts=dict(counts),
                invalid_reasons=dict(causes))

def generate(args):
    requests = read(args.requests)
    config = dict(model=args.model, max_output_tokens=args.max_tokens,
                  backend="openai_responses", request_hash=digest(requests))
    prior = read(args.out) if Path(args.out).exists() else []
    if any(r["config_id"] != digest(config) for r in prior):
        raise ValueError("Use a new output file for a different model, request set or configuration")
    if not args.execute:
        return dict(dry_run=True, requests=len(requests), limit=args.limit, config=config)
    done = {r["request_id"] for r in prior if r["status"] in ("ok", "incomplete")}
    pending = [q for q in requests if q["request_id"] not in done][:args.limit]
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with Path(args.out).open("a") as out:
        for q in pending:
            result = dict(request_id=q["request_id"], model=args.model, config_id=digest(config),
                          config=config, created_at=time.time())
            try:
                response = api_call(args.model, q["messages"], args.max_tokens)
                response.pop("raw", None)
                result.update(response)
            except (urllib.error.URLError, TimeoutError) as ex:
                result.update(status="technical_failure", error_type=type(ex).__name__)
            result["response_id"] = digest(result)
            out.write(json.dumps(result, ensure_ascii=False)+"\n"); out.flush()
            if result["status"] == "technical_failure":
                break
    return dict(output=str(args.out))

def judge(args):
    batch = read(args.jobs)
    config = dict(model=args.model, rubric=RUBRIC, max_tokens=args.max_tokens,
                  jobs_hash=digest(batch), backend="anthropic_messages")
    prior = read(args.out) if Path(args.out).exists() else []
    if any(r["config_id"] != digest(config) for r in prior):
        raise ValueError("Use a new output file for a different judge configuration")
    if not args.execute:
        return dict(dry_run=True, jobs=len(batch), limit=args.limit, model=args.model)
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise ValueError("Set ANTHROPIC_API_KEY locally")
    done = {r["job_id"] for r in prior}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with Path(args.out).open("a") as out:
        for job in [j for j in batch if j["job_id"] not in done][:args.limit]:
            body = dict(model=args.model, max_tokens=args.max_tokens, system=RUBRIC,
                        messages=[dict(role="user", content=json.dumps(job["payload"], ensure_ascii=False))])
            request = urllib.request.Request("https://api.anthropic.com/v1/messages",
                       data=json.dumps(body).encode(), headers={"x-api-key":key,
                       "anthropic-version":"2023-06-01", "Content-Type":"application/json"})
            record = dict(job_id=job["job_id"], response_id=job["response_id"],
                          order=job["order"], config_id=digest(config), model=args.model,
                          created_at=time.time())
            try:
                with urllib.request.urlopen(request, timeout=180, context=ssl.create_default_context()) as response:
                    raw = json.load(response)
                text = "\n".join(x["text"] for x in raw.get("content",[]) if x["type"]=="text")
                record["text"] = text
                if raw.get("stop_reason") != "end_turn":
                    record["status"] = "incomplete"
                else:
                    record["status"], record["label"] = check(text, job)
            except (urllib.error.URLError, TimeoutError) as ex:
                record.update(status="technical_failure", error_type=type(ex).__name__)
            out.write(json.dumps(record, ensure_ascii=False)+"\n"); out.flush()
            if record["status"] == "technical_failure":
                break
    return dict(output=str(args.out))

def prepare_expertqa(args):
    spec = json.loads((ROOT/"data/expertqa_selection.json").read_text())
    import hashlib
    path = Path(args.source)
    if hashlib.sha256(path.read_bytes()).hexdigest() != spec["source_sha256"]:
        raise ValueError("ExpertQA source checksum differs from the historical snapshot")
    source = read(path)
    cues = json.loads((ROOT/"data/cues_v1.json").read_text())
    output = []
    for selection in spec["rows"]:
        r = source[selection["source_row_zero_based"]]
        area = selection["area"]
        conditions = [("C0",None),("C3",selection["mismatched_field"])]
        if area != "unrelated":
            conditions = [("C0",None),("C1",area),("C2",area),("C3",selection["mismatched_field"])]
        for condition,field in conditions:
            output.append(seal(dict(question=r["question"], condition=condition, cue_field=field,
                source_row_zero_based=selection["source_row_zero_based"], area=area,
                messages=messages(r["question"],condition,field,cues[field] if field else None))))
    write(args.out, output)
    return dict(requests=len(output), output=args.out)

def main():
    if not __debug__:
        raise RuntimeError("Do not run with -O: the preserved historical validator uses assertions")
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("audit")
    j = sub.add_parser("export-judge")
    j.add_argument("--requests",default=str(ROOT/"data/pilot_requests.jsonl"))
    j.add_argument("--responses",default=str(ROOT/"data/pilot_responses.jsonl"))
    j.add_argument("--out",required=True)
    g = sub.add_parser("generate")
    g.add_argument("--requests",default=str(ROOT/"data/pilot_requests.jsonl"))
    g.add_argument("--model",required=True);g.add_argument("--out",required=True)
    g.add_argument("--max-tokens",type=int,default=4096)
    g.add_argument("--limit",type=int,default=1);g.add_argument("--execute",action="store_true")
    j = sub.add_parser("judge-legacy")
    j.add_argument("--jobs",required=True);j.add_argument("--out",required=True)
    j.add_argument("--model",required=True);j.add_argument("--max-tokens",type=int,default=2048)
    j.add_argument("--limit",type=int,default=1);j.add_argument("--execute",action="store_true")
    x = sub.add_parser("prepare-expertqa")
    x.add_argument("--source",required=True);x.add_argument("--out",required=True)
    args = p.parse_args()
    if getattr(args,"limit",1) < 1:
        p.error("--limit must be positive")
    if args.command=="audit":result=audit()
    elif args.command=="export-judge":
        rows=list(jobs(read(args.requests),read(args.responses)));write(args.out,rows)
        result=dict(jobs=len(rows),output=args.out)
    elif args.command=="generate":result=generate(args)
    elif args.command=="judge-legacy":result=judge(args)
    else:result=prepare_expertqa(args)
    print(json.dumps(result,indent=2))
if __name__=="__main__":main()
