"""Bridge FIRERPA's xfrpc CLI to the pinned official Android FRP client.

Executed by the existing FIRERPA supervisor; it does not start another daemon,
change credentials, or restart the phone. The native client owns reconnection.
"""
import os
from pathlib import Path
import sys


def official_args(argv):
    result = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ('-f', '-s'):
            pass  # Official frpc already stays in the foreground.
        elif arg in ('-v', '--version'):
            return ['--version']
        elif arg in ('-h', '--help'):
            return ['--help']
        elif arg == '-c' and i + 1 < len(argv):
            i += 1
            result.extend(['-c', argv[i]])
        else:
            raise ValueError('Unsupported forwarding client argument')
        i += 1
    if '-c' not in result:
        raise ValueError('Missing forwarding configuration')
    return result


def main():
    binary = Path(os.environ['USRDIR']) / 'phone-pilot/frpc-0.72.0-android-arm64'
    args = official_args(sys.argv[1:])
    os.execv(str(binary), [str(binary), *args])


if __name__ == '__main__':
    try:
        main()
    except (KeyError, ValueError, OSError):
        # Never echo command/config values: they can contain device credentials.
        print('BK forwarding adapter: invalid installation or arguments', file=sys.stderr)
        sys.exit(1)
