"""Original pilot validator, intentionally unchanged. Run without python -O."""
def validate(label,job):
 assert label['behavior'] in ['one_sense','multi_sense','clarify','decline']
 ids={x['sense_id'] for x in job['payload']['reference_senses']}
 assert label['selected_sense_id'] in ids|{None,'other'}
 assert type(label['clarification_invitation']) is bool and type(label['borderline']) is bool
 assert isinstance(label['rationale'],str) and isinstance(label['content_notes'],list)
 entries=label['sense_evaluations'];assert len(entries)==2 and {x['sense_id'] for x in entries}==ids
 if label['behavior']!='one_sense':assert label['selected_sense_id'] is None
 for x in entries:
  for k in ['mentioned','essential_meaning_preserved','substantive_contradiction']:assert type(x[k]) is bool
  assert type(x['score']) is int and x['score'] in (0,1)
  expected=int(label['behavior']=='one_sense' and label['selected_sense_id']==x['sense_id'] and x['essential_meaning_preserved'] and not x['substantive_contradiction'])
  assert x['score']==expected
 return label
