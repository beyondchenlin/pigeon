"""Install the BK native bridge into one authorized FIRERPA runtime.

This installer never restarts FIRERPA or changes Android permissions.  The
caller must arrange an authorized reload and verify the live guard afterwards.
Unknown files are never overwritten; all four files are checked before writing.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile

FILES = {
    'bk_ai_phone.py': ('bk_ai_phone.py', 'bk_ai_phone_mcp_candidate.py'),
    'bk_native_mcp.py': ('bk_native_mcp.py',),
    'bk_ai_guard_extension.py': ('extension/bk_ai_guard_extension.py',),
}
BUNDLE_HASH = '03841b518f9a1308de3af62ae2fddbf4b5c0b918fa4e4aa701c5771a720fd05f'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def atomic_write(path, data):
    fd, name = tempfile.mkstemp(prefix='.bk-native-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(name, 0o600)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _install(runtime_root, user_root, source_root, *, boundary=Path('/data/local/tmp'), uid=None, prior_guard=None):
    if (os.getuid() if uid is None else uid) != 2000:
        raise RuntimeError('authorized Android shell required')
    boundary = Path(boundary).resolve()
    runtime, user = Path(runtime_root).resolve(), Path(user_root).resolve()
    if any(path == boundary or not path.is_relative_to(boundary) for path in (runtime, user)):
        raise RuntimeError('runtime outside allowed boundary')
    bundle = runtime / 'lib/python3.12/site-packages/lamda/web/bundle.cpython-312.so'
    if not bundle.is_file() or digest(bundle.read_bytes()) != BUNDLE_HASH:
        raise RuntimeError('unverified FIRERPA runtime')
    modules, state = user / 'modules', user / 'phone-pilot'
    for path in (modules, modules / 'extension', state):
        if path.is_symlink():
            raise RuntimeError('module directory is a link')
        path.mkdir(mode=0o700, exist_ok=True)
    manifest_path = state / 'bk-native-bridge-install.json'
    if manifest_path.is_symlink():
        raise RuntimeError('manifest is a link')
    try:
        manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {'files': {}}
        owned = manifest['files']
        if not isinstance(owned, dict):
            raise ValueError()
    except (OSError, ValueError, KeyError, TypeError):
        raise RuntimeError('invalid installation manifest') from None
    targets = []
    for source_name, destinations in FILES.items():
        source = Path(source_root) / source_name
        if source.is_symlink() or not source.is_file():
            raise RuntimeError('missing or linked source')
        data = source.read_bytes()
        compile(data, source_name, 'exec')
        for relative in destinations:
            target = modules / relative
            if target.is_symlink():
                raise RuntimeError('target is a link')
            previous = target.read_bytes() if target.exists() else None
            if previous is not None and digest(previous) not in (owned.get(relative), digest(data)):
                raise RuntimeError('existing file is not owned: ' + relative)
            targets.append((target, relative, data, previous))
    old_manifest = manifest_path.read_bytes() if manifest_path.exists() else None
    changed = []
    try:
        for target, relative, data, previous in targets:
            if previous != data:
                atomic_write(target, data)
                changed.append((target, previous))
            owned[relative] = digest(data)
        manifest.update(installed=True, version=2)
        if changed:
            manifest.update(reload_needed=True, prior_guard=prior_guard)
        atomic_write(manifest_path, json.dumps(manifest, sort_keys=True).encode())
    except BaseException:
        for target, previous in reversed(changed):
            if previous is None:
                target.unlink(missing_ok=True)
            else:
                atomic_write(target, previous)
        if old_manifest is not None:
            atomic_write(manifest_path, old_manifest)
        raise
    return {'installed': True, 'changed': bool(changed), 'restart_required': manifest.get('reload_needed', False)}


def install(runtime_root, user_root, source_root, *, boundary=Path('/data/local/tmp'), uid=None):
    if (os.getuid() if uid is None else uid) != 2000:
        raise RuntimeError('authorized Android shell required')
    boundary, user = Path(boundary).resolve(), Path(user_root).resolve()
    if user == boundary or not user.is_relative_to(boundary) or (user / 'phone-pilot').is_symlink():
        raise RuntimeError('user directory outside allowed boundary')
    bridge = Path(source_root) / 'bk_ai_phone.py'
    if bridge.is_symlink() or not bridge.is_file():
        raise RuntimeError('missing or linked source')
    spec = importlib.util.spec_from_file_location('_bk_native_installer_guard', bridge)
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    # Use the same lock as dispatch and manual takeover; never replace files
    # while either owns the phone.
    with guard.state(user / 'phone-pilot' / 'bk-ai') as data:
        if data.get('ai') or data.get('manual'):
            raise RuntimeError('phone is busy')
        previous = {'pid': data.get('guard_pid'), 'identity': data.get('guard_identity')}
        return _install(runtime_root, user_root, source_root, boundary=boundary, uid=uid, prior_guard=previous)


def confirm_activation(user_root, source_root, *, boundary=Path('/data/local/tmp'), uid=None):
    if (os.getuid() if uid is None else uid) != 2000:
        raise RuntimeError('authorized Android shell required')
    boundary, user = Path(boundary).resolve(), Path(user_root).resolve()
    if user == boundary or not user.is_relative_to(boundary) or (user / 'phone-pilot').is_symlink():
        raise RuntimeError('user directory outside allowed boundary')
    bridge = Path(source_root) / 'bk_ai_phone.py'
    if bridge.is_symlink() or not bridge.is_file():
        raise RuntimeError('missing or linked source')
    spec = importlib.util.spec_from_file_location('_bk_native_confirm_guard', bridge)
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    root = user / 'phone-pilot' / 'bk-ai'
    manifest_path = user / 'phone-pilot' / 'bk-native-bridge-install.json'
    with guard.state(root) as data:
        if manifest_path.is_symlink() or not manifest_path.is_file():
            raise RuntimeError('installation manifest unavailable')
        manifest = json.loads(manifest_path.read_text())
        current = {'pid': data.get('guard_pid'), 'identity': data.get('guard_identity')}
        if not guard.native_guard_ready(root) or (manifest.get('reload_needed') and current == manifest.get('prior_guard')):
            raise RuntimeError('updated guard not yet loaded')
        for destinations in FILES.values():
            for relative in destinations:
                path = user / 'modules' / relative
                if path.is_symlink() or not path.is_file() or digest(path.read_bytes()) != manifest.get('files', {}).get(relative):
                    raise RuntimeError('installed file changed: ' + relative)
        manifest['reload_needed'] = False
        atomic_write(manifest_path, json.dumps(manifest, sort_keys=True).encode())
    return {'installed': True, 'restart_required': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-root', default=os.environ.get('ROOTDIR'))
    parser.add_argument('--user-root', default=os.environ.get('USRDIR'))
    parser.add_argument('--confirm', action='store_true')
    options = parser.parse_args()
    source = Path(__file__).resolve().parent
    print(json.dumps(confirm_activation(options.user_root, source) if options.confirm
                     else install(options.runtime_root, options.user_root, source)))
