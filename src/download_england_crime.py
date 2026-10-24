"""Download one official Police.uk month, retaining the original ZIP and receipt."""
import argparse
import hashlib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests


def download(month=None):
    session = requests.Session()
    base = 'https://data.police.uk/data/'
    month = month or session.get('https://data.police.uk/api/crime-last-updated', timeout=60).json()['date'][:7]
    target = Path('data') / f'england-crime-{month}.zip'
    target.parent.mkdir(exist_ok=True)
    if target.exists():
        print(target, flush=True)
        return target
    response = session.get(base, timeout=60)
    response.raise_for_status()
    token = re.search(r"name='csrfmiddlewaretoken' value='([^']+)'", response.text)[1]
    forces = re.findall(r'name="forces" value="([^"]+)"', response.text)
    # Request all forces; England is selected by E01 LSOA codes during processing.
    fields = [('csrfmiddlewaretoken', token), ('date_from', month), ('date_to', month), ('include_crime', 'on')]
    fields += [('forces', force) for force in forces]
    response = session.post(base, data=fields, headers={'Referer': base}, timeout=120)
    response.raise_for_status()
    print('Download job:', response.url, flush=True)
    page = response.text
    Path('data/download_job.html').write_text(page, encoding='utf-8')
    progress_path = re.search(r'"url": "([^"]+)"', page)[1]
    for attempt in range(300):
        status_response = session.get(urljoin(base, progress_path), timeout=60)
        status_response.raise_for_status()
        status = status_response.json()
        if status['status'] == 'ready':
            break
        if status['status'] == 'error':
            raise RuntimeError('Police.uk download generation failed')
        time.sleep(2)
    else:
        raise TimeoutError('Police.uk download not ready; job URL retained in data/download_job.html')
    partial = target.with_suffix('.zip.part')
    with session.get(status['url'], stream=True, timeout=(30, 120)) as archive:
        archive.raise_for_status()
        with partial.open('wb') as handle:
            for chunk in archive.iter_content(1024 * 1024):
                handle.write(chunk)
    import zipfile
    with zipfile.ZipFile(partial) as archive:
        if archive.testzip() is not None:
            raise ValueError('Corrupt source ZIP')
    partial.replace(target)
    receipt = {'month': month, 'source': base, 'job_url': response.url,
               'download_url': status['url'], 'requested_forces': forces,
               'retrieved_at': datetime.now(timezone.utc).isoformat(),
               'sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
               'bytes': target.stat().st_size, 'licence': 'Open Government Licence v3.0'}
    target.with_suffix('.metadata.json').write_text(json.dumps(receipt, indent=2), encoding='utf-8')
    print(f'Downloaded {target} ({receipt["bytes"]:,} bytes)', flush=True)
    return target


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--month')
    download(parser.parse_args().month)
