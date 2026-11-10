"""Run FIRERPA's configuration generator with a separate, pinned FRP client.

Select this file through the generated Supervisor fwd program configuration.
Native runtime files remain intact.
The normal supervisor owns the single client process and its lifecycle.
"""
import hashlib
import os
from pathlib import Path

from bk_frpc_adapter import official_args, write_recovery_config

NATIVE_SHA = 'd530185b1e5014661195eab23889d3bb0e4d35cc4c1aa33f149dfa5b4fa9e689'
CLIENT_SHA = 'c31826e2f29b2efacf97577b15e7d7e5937ab5d4a72815343c22bb275c57432c'


class NativeExec:
    def __init__(self, root, user):
        self.root, self.user = Path(root).resolve(), Path(user).resolve()

    def __getattr__(self, key):
        return getattr(os, key)

    def execlp(self, file, arg0, *args):
        if file != 'xfrpc' or arg0 != 'xfrpc':
            raise RuntimeError('Unexpected native forwarder command')
        native = self.root/'bin/xfrpc'
        client = self.user/'phone-pilot/frpc-0.72.0-bk.1-android-arm64'
        if hashlib.sha256(native.read_bytes()).hexdigest() != NATIVE_SHA:
            raise RuntimeError('Native forwarder changed; revalidation required')
        if hashlib.sha256(client.read_bytes()).hexdigest() != CLIENT_SHA:
            raise RuntimeError('Unverified recovery client')
        argv = official_args(list(args))
        index = argv.index('-c') + 1
        source = Path(argv[index]).resolve()
        if source != self.user/'.local/run/fwd.toml':
            raise RuntimeError('Unexpected native forwarding configuration')
        argv[index] = write_recovery_config(source, client.parent)
        os.execv(str(client), [str(client), *argv])


def main():
    if os.getuid() != 2000:
        raise RuntimeError('Only the authorized non-root shell runtime is supported')
    import lamda.services.fwd as native
    os.chdir(Path(os.environ['USRDIR']).resolve()/'.local/run')
    # Scope interception to this service module; no global os/subprocess patch.
    native.os = NativeExec(os.environ['ROOTDIR'], os.environ['USRDIR'])
    native.main()


if __name__ == '__main__':
    main()
