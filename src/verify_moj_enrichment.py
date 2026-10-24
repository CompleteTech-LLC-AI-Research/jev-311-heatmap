"""Reconcile real aggregate margins and independently recount detailed weapon codes."""
import csv
import io
import json
import zipfile
from collections import Counter
from pathlib import Path


def verify():
    data = json.loads(Path('data/moj-convictions-2026.json').read_text(encoding='utf-8'))
    records = data['records']
    for record in records:
        for dimension in ('sex', 'age', 'ethnicity', 'detailed_ethnicity', 'quarter'):
            assert sum(record[dimension].values()) == record['total'], (record['code'], dimension)
    assert sum(r['total'] for r in records) == data['metadata']['audit']['selected_convictions']
    english = set(data['metadata']['area_counts'])
    code_totals = Counter()
    source_persons = Counter()
    # Independent source pass: do not reuse the preparation functions or keys.
    with zipfile.ZipFile('data/moj-outcomes-2026-q1.zip') as archive:
        stream = io.TextIOWrapper(archive.open('obo_pros_conv_sent.csv'), encoding='utf-8-sig')
        for row in csv.DictReader(stream):
            if row['Year ending March'] != 'Year ending March 2026':
                continue
            if row['Person/Other'] != '01: Person' or row['Police Force Area'] not in english:
                continue
            count = int(row['Convicted'])
            source_persons[row['Police Force Area']] += count
            if row['Offence Group'] == '07: Possession of weapons':
                code_totals[row['HO Offence Code']] += count
    source_persons = Counter({k: v for k, v in source_persons.items() if v})
    assert source_persons == Counter(data['metadata']['area_counts'])
    generated_codes = Counter()
    for record in records:
        if record['group'] == 'Possession of weapons':
            generated_codes[record['code']] += record['total']
    assert Counter({k: v for k, v in code_totals.items() if v}) == generated_codes
    result = {'demographic_marginals_checked': len(records) * 5,
              'English_area_totals_checked': len(source_persons),
              'weapon_codes_independently_recounted': len(generated_codes),
              'weapon_conviction_occasions': sum(generated_codes.values()),
              'English_person_conviction_occasions': sum(source_persons.values()),
              'status': 'passed'}
    Path('results/england/2026-07/enrichment_verification.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    verify()
