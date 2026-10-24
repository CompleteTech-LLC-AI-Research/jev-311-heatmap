"""Aggregate verified MoJ convictions for English police-force areas, retaining unknowns."""
import argparse
import csv
import hashlib
import io
import json
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import requests

BOUNDARY_SERVICE = 'https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services/Police_Force_Areas_Dec_2024_EW_BGC/FeatureServer/0/query'
DIMENSIONS = {'sex': 'Sex', 'age': 'Age Range', 'ethnicity': 'Ethnicity',
              'detailed_ethnicity': 'Detailed Ethnicity', 'quarter': 'Quarter'}


def clean(text):
    return text.split(': ', 1)[-1]


def prepare(source, output, year=2026):
    receipt = json.loads(source.with_suffix('.metadata.json').read_text(encoding='utf-8'))
    digest = hashlib.sha256()
    with source.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    if digest.hexdigest() != receipt['sha256']:
        raise ValueError('MoJ archive hash does not match receipt')
    params = {'where': "PFA24CD LIKE 'E%'", 'outFields': 'PFA24CD,PFA24NM', 'outSR': 4326,
              'maxAllowableOffset': 0.001, 'f': 'geojson'}
    response = requests.get(BOUNDARY_SERVICE, params=params, timeout=120)
    response.raise_for_status()
    boundaries = response.json()
    if len(boundaries.get('features', [])) != 39 or boundaries.get('exceededTransferLimit'):
        raise ValueError('Expected all 39 English territorial police force boundaries')
    names = {}
    for feature in boundaries['features']:
        name = feature['properties']['PFA24NM']
        name = name.removesuffix(' Police').removesuffix(' Constabulary')
        name = {'Devon & Cornwall': 'Devon and Cornwall', 'London, City of': 'City of London'}.get(name, name)
        feature['properties']['moj_name'] = name
        names[name] = feature['properties']['PFA24CD']
    print('English boundary names:', sorted(names), flush=True)
    # Each key is disjoint: force / offence group / offence / detailed HO offence code.
    # Marginal dimensions are kept separately; never add these together as observations.
    slices = {}
    audit = Counter()
    area_counts = Counter()
    excluded_areas = Counter()
    label_sets = {dimension: set() for dimension in DIMENSIONS}
    wanted = f'Year ending March {year}'
    with zipfile.ZipFile(source) as archive:
        stream = io.TextIOWrapper(archive.open('obo_pros_conv_sent.csv'), encoding='utf-8-sig', errors='strict')
        for row in csv.DictReader(stream):
            audit['rows_read'] += 1
            if audit['rows_read'] % 1000000 == 0:
                print(f'Read {audit["rows_read"]:,} rows; selected {audit["selected_convictions"]:,} convictions', flush=True)
            if row['Year ending March'] != wanted:
                continue
            audit['latest_year_rows'] += 1
            count = int(row['Convicted'])
            if count < 0:
                raise ValueError('Negative convictions')
            if row['Person/Other'] != '01: Person':
                audit['excluded_non_person_or_unclassified_convictions'] += count
                continue
            force = row['Police Force Area']
            if force not in names:
                excluded_areas[force] += count
                continue
            if not count:
                continue
            group, offence, code = row['Offence Group'], row['Offence'], row['HO Offence Code']
            key = (force, group, offence, code)
            if key not in slices:
                slices[key] = {'total': 0, 'offence_type': clean(row['Offence Type']), **{d: Counter() for d in DIMENSIONS}}
            item = slices[key]
            if item['offence_type'] != clean(row['Offence Type']):
                raise ValueError('An offence slice has inconsistent offence types')
            item['total'] += count
            for dimension, column in DIMENSIONS.items():
                label = clean(row[column]) or 'Unknown'
                item[dimension][label] += count
                label_sets[dimension].add(label)
            area_counts[force] += count
            audit['selected_convictions'] += count
            audit['selected_nonzero_source_rows'] += 1
    if not slices:
        raise ValueError('No selected convictions')
    for item in slices.values():
        if any(sum(item[d].values()) != item['total'] for d in DIMENSIONS):
            raise ValueError('Demographic marginal does not reconcile')
    records = [{'force': force, 'group': clean(group), 'offence': offence, 'code': code, **item}
               for (force, group, offence, code), item in sorted(slices.items())]
    missing = sorted(set(names) - set(area_counts))
    # Absence is explicitly unknown, never silently treated as zero convictions.
    metadata = {'title': 'Convicted persons by police force area',
                'period_start': f'{year-1}-04-01', 'period_end': f'{year}-03-31',
                'period_label': f'April {year-1} – March {year}',
                'source_receipt': receipt, 'source_member': 'obo_pros_conv_sent.csv',
                'generated_at': datetime.now(timezone.utc).isoformat(), 'audit': dict(audit),
                'area_counts': dict(area_counts), 'excluded_area_counts': dict(excluded_areas),
                'boundary_areas_without_published_rows': missing,
                'boundary_source': response.url, 'boundary_sha256': hashlib.sha256(response.content).hexdigest(),
                'boundary_release': 'December 2024; simplified for display',
                'boundary_attribution': 'Contains OS data © Crown copyright and database right 2024; contains National Statistics data © Crown copyright and database right 2024.',
                'technical_guide': 'https://assets.publishing.service.gov.uk/media/6a689bd8f938595f17d020ec/criminal-justice-statistics-technical-guide-Q1-2026.pdf',
                'notes': ['Counts are conviction occasions for persons, not distinct people or crimes.',
                          'Only records classified as Person are included; companies/public bodies and unknown person status are excluded.',
                          'Principal offence basis; repeated court appearances can count repeatedly.',
                          'Court completion period differs from crime occurrence dates.',
                          'Geography is the published court-statistics police force area, not offender residence or exact offence location.',
                          'No record-level link to Police.uk crimes; no demographic assignment to grid cells.',
                          'Unknown demographic values are retained; missingness is displayed.',
                          'Detailed HO labels describe legal offences, not complete physical weapon specifications.',
                          'Ethnicity for summary offences is not reported reliably; prefer indictable-only and triable-either-way offences for ethnicity analysis.']}
    payload = {'metadata': metadata, 'boundaries': boundaries, 'records': records,
               'dimension_labels': {d: sorted(v) for d, v in label_sets.items()}}
    for record in records:
        record['indictable'] = record['offence_type'] in ('Indictable only', 'Triable either way')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, separators=(',', ':')), encoding='utf-8')
    output.with_suffix('.metadata.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')
    print(f'Wrote {len(records):,} offence slices, {audit["selected_convictions"]:,} conviction occasions to {output}', flush=True)
    return payload


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, default=Path('data/moj-outcomes-2026-q1.zip'))
    parser.add_argument('--output', type=Path, default=Path('data/moj-convictions-2026.json'))
    parser.add_argument('--year-ending-march', type=int, default=2026)
    args = parser.parse_args()
    prepare(args.input, args.output, args.year_ending_march)
