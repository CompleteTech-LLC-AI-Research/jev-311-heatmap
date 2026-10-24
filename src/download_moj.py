"""Retrieve the official March 2026 Outcomes by Offence CSV archive."""
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests

SOURCE = 'https://www.gov.uk/government/statistics/criminal-justice-statistics-quarterly-march-2026'


def download():
    target = Path('data/moj-outcomes-2026-q1.zip')
    target.parent.mkdir(exist_ok=True)
    if target.exists():
        print(f'Using {target}', flush=True)
        return target
    session = requests.Session()
    page = session.get(SOURCE, timeout=60)
    page.raise_for_status()
    links = re.findall(r'href="([^"]+)"', page.text)
    urls = sorted({urljoin(SOURCE, u) for u in links if u.endswith('/outcome-by-offence-csvs.zip')})
    if len(urls) != 1:
        raise ValueError(f'Ambiguous Outcomes by Offence archive: {[u for u in links if ".zip" in u.lower()]}')
    url = urls[0]
    print(f'Downloading {url}', flush=True)
    digest = hashlib.sha256()
    total = 0
    partial = target.with_suffix('.zip.part')
    with session.get(url, stream=True, timeout=(30, 120)) as response:
        response.raise_for_status()
        with partial.open('wb') as handle:
            for chunk in response.iter_content(1024 * 1024):
                handle.write(chunk)
                digest.update(chunk)
                total += len(chunk)
                if total % (25 * 1024 * 1024) == 0:
                    print(f'{total // (1024 * 1024)} MiB', flush=True)
    import zipfile
    with zipfile.ZipFile(partial) as archive:
        print([(i.filename, i.file_size) for i in archive.infolist()], flush=True)
    partial.replace(target)
    metadata = {'source': SOURCE, 'url': url, 'bytes': total, 'sha256': digest.hexdigest(),
                'retrieved_at': datetime.now(timezone.utc).isoformat(),
                'licence': 'Open Government Licence v3.0'}
    target.with_suffix('.metadata.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')
    return target


if __name__ == '__main__':
    download()
