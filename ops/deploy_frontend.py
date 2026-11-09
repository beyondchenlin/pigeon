"""Publish a built Vue frontend to the existing pilot without restarting services."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile

ROOT = Path('/opt/phone-pilot-20261108/pigeon')
CONTAINER = 'phone-pilot-pigeon-pigeon-1'
NGINX = '/usr/local/openresty/nginx/sbin/nginx'
PUBLIC_ROOT = '/user/phone-pilot/frontend/current'


def nginx(*args):
    subprocess.run(['docker', 'exec', CONTAINER, NGINX, *args], check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--revision', required=True)
    args = parser.parse_args()
    if not re.fullmatch(r'[0-9a-f]{40}', args.revision):
        raise ValueError('Expected the full dev commit SHA')
    with args.archive.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    if digest != args.sha256.lower():
        raise ValueError('Archive SHA-256 mismatch')

    frontend = ROOT / 'data/user/phone-pilot/frontend'
    release = frontend / 'releases' / args.revision
    current = frontend / 'current'
    temporary_link = frontend / 'current.next'
    if release.exists() or temporary_link.exists() or temporary_link.is_symlink():
        raise FileExistsError('Release or pending switch exists; inspect before retrying')
    if current.exists() and not current.is_symlink():
        raise ValueError('Current frontend must be a symlink')
    config = ROOT / 'single-phone-nginx.conf'
    old_config = config.read_text()
    # Replace only the static root. Authentication and phone proxy rules stay intact.
    new_config, count = re.subn(
        r'root\s+(?:/var/www/html|/user/phone-pilot/frontend/current);',
        f'root {PUBLIC_ROOT};', old_config,
    )
    if count != 1:
        raise ValueError('Expected exactly one known static root')
    old_target = os.readlink(current) if current.is_symlink() else None

    with tarfile.open(args.archive, 'r:gz') as archive:
        members = archive.getmembers()
        if sum(item.size for item in members) > 100 * 1024 * 1024:
            raise ValueError('Frontend exceeds the 100 MiB release limit')
        for item in members:
            destination = (release / item.name).resolve()
            if not destination.is_relative_to(release.resolve()):
                raise ValueError('Archive path escapes release')
            if not (item.isfile() or item.isdir()):
                raise ValueError('Archive must contain regular files/directories only')
        release.mkdir(parents=True)
        # Explicit extraction also supports Debian's Python without tar data_filter.
        for item in members:
            destination = release / item.name
            if item.isdir():
                destination.mkdir(parents=True, exist_ok=True)
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(item) as source, destination.open('xb') as output:
                    shutil.copyfileobj(source, output)

    index = (release / 'index.html').read_text()
    if '<title>BK群控</title>' not in index:
        raise ValueError('Archive is not the BK frontend')
    for reference in re.findall(r'(?:src|href)="([^"]+)"', index):
        if reference.startswith(('https:', 'http:', 'data:', '#')):
            continue
        asset = (release / reference.lstrip('/')).resolve()
        if not asset.is_relative_to(release.resolve()) or not asset.is_file():
            raise ValueError(f'Missing or invalid frontend asset: {reference}')
    if not (release / 'upstream-license.txt').is_file():
        raise ValueError('Missing upstream license')
    (release / 'release.json').write_text(json.dumps({
        'revision': args.revision, 'branch': 'dev', 'archive_sha256': digest,
    }) + '\n')

    # Keep only the small recovery record; the prior build remains in its existing location.
    recovery = frontend / f'before-{args.revision}.json'
    recovery.write_text(json.dumps({'previous_target': old_target, 'nginx': old_config}) + '\n')
    try:
        temporary_link.symlink_to(Path('releases') / args.revision)
        temporary_link.replace(current)
        # Write in place: this file is already bind-mounted in the live container.
        config.write_text(new_config)
        nginx('-t')
        nginx('-s', 'reload')
    except BaseException:
        config.write_text(old_config)
        if old_target is None:
            current.unlink(missing_ok=True)
        else:
            temporary_link.unlink(missing_ok=True)
            temporary_link.symlink_to(old_target)
            temporary_link.replace(current)
        nginx('-t')
        nginx('-s', 'reload')
        raise
    print(json.dumps({'release': str(release), 'revision': args.revision, 'frontend_only': True}))


if __name__ == '__main__':
    main()
