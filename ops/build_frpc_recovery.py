"""Reproducibly build the small BK reconnect policy patch on pinned FRP source.

Uses the existing Go module/build caches. Build products belong in a task directory,
not Git. No phone/server configuration or credentials are used by this builder.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

MODULE = 'github.com/fatedier/frp@v0.72.0'
SUM = 'h1:BP0T6a0gdCqQoFP3iKngQMUtiTcHwvEGKtTb+uNYvaA='
VERSION = '0.72.0-bk.1'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    root = args.directory.resolve()
    root.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(root).free < 12 * 1024**3:
        raise RuntimeError('Keep at least 10 GiB beyond the estimated 1.5 GiB build peak')
    info = json.loads(subprocess.check_output(['go','mod','download','-json',MODULE], text=True))
    if info.get('Sum') != SUM:
        raise RuntimeError('Pinned source module checksum mismatch')
    source = root / 'source'
    if source.exists():
        raise RuntimeError('Source already exists; reuse/review the existing build rather than overwrite')
    shutil.copytree(info['Dir'], source)
    shutil.copyfile(source / 'LICENSE', root / 'FRP-LICENSE')
    service = source / 'client/service.go'
    text = service.read_text(encoding='utf8')
    declaration = 'func (svr *Service) loopLoginUntilSuccess(maxInterval time.Duration, firstLoginExit bool) {'
    cap = '\t\t\tMaxDuration:     20 * time.Second,'
    assert text.count(declaration) == 1 and text.count(cap) == 1
    service.chmod(0o600)
    service.write_text(text.replace(cap, '\t\t\tMaxDuration:     3 * time.Second,').replace(
        declaration, declaration + '\n\tmaxInterval = bkReconnectInterval(maxInterval)'), encoding='utf8')
    (source / 'client/bk_reconnect.go').write_text('''package client
import "time"
// Bound the otherwise fixed 10/20 second backoff for a small mobile-device fleet.
// Detection, authentication, jitter and in-process reconnect remain upstream code.
func bkReconnectInterval(max time.Duration) time.Duration {
 if max > 3*time.Second { return 3*time.Second }; return max
}
''', encoding='utf8')
    (source / 'client/bk_reconnect_test.go').write_text('''package client
import ("testing"; "time")
func TestBkReconnectInterval(t *testing.T) {
 for _, c := range []struct{in, want time.Duration}{{time.Second,time.Second},{10*time.Second,3*time.Second},{20*time.Second,3*time.Second}} {
  if got:=bkReconnectInterval(c.in);got!=c.want {t.Fatalf("%v -> %v, want %v",c.in,got,c.want)}
 }
}
''', encoding='utf8')
    subprocess.run(['go','test','-tags','frpc,noweb','./client','-run','TestBkReconnectInterval','-count=1'],cwd=source,check=True)
    binary = root / ('frpc-' + VERSION + '-android-arm64')
    env = dict(os.environ, CGO_ENABLED='0', GOOS='android', GOARCH='arm64')
    subprocess.run(['go','build','-trimpath','-tags','frpc,noweb','-ldflags',
                    '-s -w -X github.com/fatedier/frp/pkg/util/version.version='+VERSION,
                    '-o',str(binary),'./cmd/frpc'],cwd=source,env=env,check=True)
    with binary.open('rb') as stream:
        digest = hashlib.file_digest(stream,'sha256').hexdigest()
    manifest = {'version':VERSION,'module':MODULE,'module_sum':SUM,'upstream_commit':info['Origin']['Hash'],
                'go':subprocess.check_output(['go','version'],text=True).strip(),
                'sha256':digest,
                'bytes':binary.stat().st_size,'policy':'reconnect backoff cap 3s; existing jitter retained'}
    (root / 'build-manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf8')
    print(json.dumps(manifest),flush=True)


if __name__ == '__main__':
    main()
