"""Portable helpers extracted from the October 2026 pilot; standard library only."""

import collections, hashlib, json, os, ssl, urllib.request, urllib.error

from pathlib import Path

LABELS = dict(computer_science='computer science', medicine_healthcare='medicine/healthcare', education_psychology='education/psychology', statistics='statistics')

def digest(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

def read(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]

def write(path, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x') as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + '\n')

def messages(question, condition, field=None, cue=None):
    if condition == 'C0':
        return [{'role': 'user', 'content': question}]
    if condition in ('C1', 'C3'):
        return [{'role': 'user', 'content': f'My academic field is {LABELS[field]}. {question}'}]
    if condition == 'C2':
        return [{'role': 'user', 'content': cue}, {'role': 'assistant', 'content': 'Understood.'},
                {'role': 'user', 'content': question}]
    raise ValueError('Unknown condition')

def seal(row):
    row['request_id'] = digest(row)
    return row

def api_call(model, prompt, max_tokens):
    key = os.environ.get('OPENAI_API_KEY')
    if not key:
        raise ValueError('Set OPENAI_API_KEY in your local environment; never put it in a data file')
    body = dict(model=model, input=prompt, max_output_tokens=max_tokens, store=False)
    request = urllib.request.Request('https://api.openai.com/v1/responses',
        data=json.dumps(body).encode(), headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
    cafile = '/etc/ssl/cert.pem' if Path('/etc/ssl/cert.pem').exists() else None
    with urllib.request.urlopen(request, timeout=180, context=ssl.create_default_context(cafile=cafile)) as response:
        raw = json.load(response)
    parts = [p for o in raw.get('output', []) if o.get('type') == 'message' for p in o.get('content', [])]
    text = '\n'.join(p.get('text', p.get('refusal', '')) for p in parts)
    status = 'ok' if raw.get('status') == 'completed' and text.strip() else 'incomplete'
    return dict(text=text, status=status, raw=raw, returned_model=raw.get('model'),
                effective_parameters={'max_output_tokens': max_tokens, 'store': False, 'other_settings': 'provider_defaults'})

def kappa(a, b):
    if len(a) != len(b) or not a:
        raise ValueError('Need aligned nonempty human/judge labels')
    ca, cb = collections.Counter(a), collections.Counter(b)
    observed = sum(x == y for x,y in zip(a,b)) / len(a)
    chance = sum(ca[x] * cb[x] for x in set(a+b)) / len(a)**2
    return None if chance == 1 else (observed-chance)/(1-chance)
