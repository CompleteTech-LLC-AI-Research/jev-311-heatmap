"""Build an England-only recorded-crime map from an official Police.uk ZIP."""
import argparse
import csv
import hashlib
import io
import json
import math
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from pyproj import Transformer


def build(source, output, cell_m=2000, convictions=None):
    if cell_m < 250:
        raise ValueError('Cell size must be at least 250 metres')
    receipt = json.loads(source.with_suffix('.metadata.json').read_text(encoding='utf-8'))
    if hashlib.sha256(source.read_bytes()).hexdigest() != receipt['sha256']:
        raise ValueError('Source ZIP does not match its receipt')
    forward = Transformer.from_crs(4326, 27700, always_xy=True)
    inverse = Transformer.from_crs(27700, 4326, always_xy=True)
    cells = defaultdict(Counter)
    counts, categories, forces = Counter(), Counter(), defaultdict(Counter)
    ids = set()
    files = []
    with zipfile.ZipFile(source) as archive:
        for name in sorted(archive.namelist()):
            if not name.endswith('-street.csv'):
                continue
            files.append(name)
            for row in csv.DictReader(io.TextIOWrapper(archive.open(name), encoding='utf-8-sig')):
                counts['source_rows'] += 1
                if row['Month'] != receipt['month']:
                    raise ValueError('Unexpected month in source')
                lsoa = row['LSOA code']
                if not lsoa.startswith('E01'):
                    counts['non_england_or_missing_lsoa'] += 1
                    if not lsoa:
                        counts['missing_lsoa'] += 1
                    continue
                crime_id = row['Crime ID']
                if crime_id:
                    identity = (row['Reported by'], crime_id)
                    if identity in ids:
                        counts['duplicate_crime_ids'] += 1
                        continue
                    ids.add(identity)
                counts['england_rows'] += 1
                force = row['Reported by']
                forces[force]['england_rows'] += 1
                try:
                    lon, lat = float(row['Longitude']), float(row['Latitude'])
                    if not (math.isfinite(lon) and math.isfinite(lat) and -7 < lon < 2.5 and 49.8 < lat < 56):
                        raise ValueError()
                except (ValueError, TypeError):
                    counts['invalid_coordinates'] += 1
                    continue
                x, y = forward.transform(lon, lat)
                category = row['Crime type']
                cells[(math.floor(x / cell_m), math.floor(y / cell_m))][category] += 1
                categories[category] += 1
                forces[force]['mapped_rows'] += 1
                counts['mapped_rows'] += 1
    if not cells:
        raise ValueError('No mappable English records')
    category_names = sorted(categories)
    features = []
    for (gx, gy), values in sorted(cells.items()):
        corners = [(gx * cell_m, gy * cell_m), ((gx + 1) * cell_m, gy * cell_m),
                   ((gx + 1) * cell_m, (gy + 1) * cell_m), (gx * cell_m, (gy + 1) * cell_m)]
        polygon = [[round(lon, 6), round(lat, 6)] for lon, lat in (inverse.transform(x, y) for x, y in corners)]
        polygon.append(polygon[0])
        features.append({'type': 'Feature', 'geometry': {'type': 'Polygon', 'coordinates': [polygon]},
                         'properties': {'counts': [values[name] for name in category_names]}})
    mapped_crime = counts['mapped_rows'] - categories.get('Anti-social behaviour', 0)
    supplied = {Path(name).name.removeprefix(receipt['month'] + '-').removesuffix('-street.csv') for name in files}
    excluded_forces = {'dyfed-powys', 'gwent', 'north-wales', 'south-wales', 'northern-ireland'}
    missing_forces = sorted(set(receipt['requested_forces']) - supplied - excluded_forces)
    metadata = {'title': 'England recorded crime', 'month': receipt['month'],
                'generated_at': datetime.now(timezone.utc).isoformat(), 'cell_m': cell_m,
                'selection': 'LSOA code starts with E01; records without LSOA excluded',
                'counts': dict(counts), 'mapped_crime_excluding_asb': mapped_crime,
                'missing_requested_english_or_transport_forces': missing_forces,
                'category_counts': dict(categories), 'occupied_cells': len(cells),
                'forces': dict(forces), 'source_files': files, 'source_receipt': receipt,
                'notes': ['Counts are not population-adjusted crime rates or safety scores.',
                          'Published locations are anonymised approximations.',
                          'ASB is separate from crime and excluded from the initial view.',
                          'Missing or unpublished records cannot be treated as zero crime.',
                          'Grid cells can straddle country boundaries; membership uses the source LSOA, not the cell extent.']}
    payload = {'metadata': metadata, 'categories': category_names,
               'geojson': {'type': 'FeatureCollection', 'features': features}}
    if convictions:
        payload['convictions'] = json.loads(convictions.read_text(encoding='utf-8'))
        metadata['convictions_enrichment'] = payload['convictions']['metadata']
    output.mkdir(parents=True, exist_ok=True)
    template = Path(__file__).with_name('england_crime_map.html').read_text(encoding='utf-8')
    template = template.replace('__ENRICHMENT_SCRIPT__', Path(__file__).with_name('convictions_panel.js').read_text(encoding='utf-8'))
    encoded = json.dumps(payload, separators=(',', ':')).replace('</', '<\\/')
    (output / 'index.html').write_text(template.replace('__PAYLOAD__', encoded), encoding='utf-8')
    (output / 'run_metadata.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')
    (output / 'crime_grid.geojson').write_text(json.dumps(payload['geojson']), encoding='utf-8')
    if convictions:
        (output / 'convictions_metadata.json').write_text(json.dumps(payload['convictions']['metadata'], indent=2), encoding='utf-8')
    print(json.dumps({'output': str(output / 'index.html'), 'counts': dict(counts),
                      'crime_excluding_asb': mapped_crime, 'cells': len(cells)}, indent=2))
    return metadata


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--cell-m', type=int, default=2000)
    parser.add_argument('--convictions', type=Path, help='Prepared MoJ aggregate JSON')
    args = parser.parse_args()
    build(args.input, args.output_dir, args.cell_m, args.convictions)
