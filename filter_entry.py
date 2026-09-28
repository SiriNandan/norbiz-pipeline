import gzip, json, random, sys
sys.path.insert(0, 'src')
from norway_company_agent.sampling import iter_bulk

with gzip.open('signalpost-universe.jsonl.gz', 'rt', encoding='utf-8') as f:
    universe = [json.loads(l) for l in f if l.strip()]

print(f'Universe size: {len(universe)}')
print('Loading registry org numbers (this may take a minute)...')

registry_orgs = set()
for record in iter_bulk('brreg-enheter.csv'):
    registry_orgs.add(record['organisation_number'])

print(f'Registry orgs loaded: {len(registry_orgs)}')

rng = random.Random(20260823)
sample = rng.sample(universe, min(1500, len(universe)))
valid = [r for r in sample if r['organisation_number'] in registry_orgs][:1000]
print(f'Valid after filter: {len(valid)}')

with open('entry-companies.jsonl', 'w', encoding='utf-8') as f:
    for r in valid:
        f.write(json.dumps(r, ensure_ascii=False) + '\n')
print('Written entry-companies.jsonl')
