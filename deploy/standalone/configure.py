"""Generate per-installation Compose settings without overwriting an existing instance."""
import argparse
from ipaddress import IPv4Address
import os
from pathlib import Path
import re
import secrets
import tempfile
from urllib.parse import urlsplit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--public-ip', type=IPv4Address, required=True, help='Host address reachable by the phones (LAN or public)')
    parser.add_argument('--lan-ip', type=IPv4Address)
    parser.add_argument('--domain', help='Host name used in the browser')
    parser.add_argument('--bind-ip', type=IPv4Address, default=IPv4Address('0.0.0.0'))
    parser.add_argument('--web-port', type=int, default=8000)
    parser.add_argument('--frp-port', type=int, default=7000)
    parser.add_argument('--mqtt-port', type=int, default=8883)
    parser.add_argument('--https', action='store_true', help='Have Caddy obtain a public certificate on ports 80/443')
    parser.add_argument('--origin', help='Browser HTTPS origin when using an existing reverse proxy')
    parser.add_argument('--turn', action='store_true', help='Run the authenticated TURN relay')
    parser.add_argument('--output', type=Path, default=Path(__file__).with_name('.env'))
    args = parser.parse_args()
    domain = args.domain or str(args.public_ip)
    label = r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?'
    if len(domain) > 253 or not re.fullmatch(label + r'(?:\.' + label + r')*', domain):
        parser.error('Invalid host name')
    if args.https and (not args.domain or '.' not in domain or re.fullmatch(r'[\d.]+', domain)):
        parser.error('--https requires a public DNS name')
    if args.https and args.origin:
        parser.error('Choose either --https or an external --origin')
    ports = {args.web_port, args.frp_port, args.mqtt_port}
    reserved = {6432, 8081, 8123, 18981, 58800, 65000}
    if args.https:
        reserved.update((80, 443))
    if args.turn:
        reserved.update((3478, *range(49160, 49201)))
    if len(ports) != 3 or ports & reserved or not all(1 <= p <= 65535 for p in ports):
        parser.error('Choose three distinct, available service ports')
    origin = args.origin or ('https://' + domain + ('' if args.https or args.web_port == 443 else f':{args.web_port}'))
    if not re.fullmatch(r'https://' + label + r'(?:\.' + label + r')*(?::[0-9]{1,5})?', origin):
        parser.error('Use an HTTPS origin containing only a host and optional port')
    try:
        if urlsplit(origin).port == 0:
            raise ValueError()
    except ValueError:
        parser.error('Invalid origin port')
    config = {
        'COMPOSE_PROJECT_NAME': 'bk-pigeon-' + secrets.token_hex(4),
        'DOMAIN': domain, 'PUBLIC_IP': args.public_ip, 'LOCAL_LAN_IP': args.lan_ip or args.public_ip,
        'PUBLIC_ORIGIN': origin, 'BIND_IP': args.bind_ip,
        'WEB_PORT': args.web_port, 'FWD_PORT': args.frp_port, 'MQP_PORT': args.mqtt_port,
        'POSTGRES_PASSWORD': secrets.token_hex(24), 'REDIS_PASSWORD': secrets.token_hex(24),
        'RABBITMQ_PASSWORD': secrets.token_hex(24), 'PIGEON_INITIAL_PASSWORD': secrets.token_hex(16),
        'BK_CLAW_RELEASE_PACKAGE_ENABLED': 0,
    }
    profiles = []
    if args.https:
        profiles.append('https')
    if args.turn:
        profiles.append('turn')
        config.update(BK_TURN_HOST=domain, BK_TURN_SECRET=secrets.token_hex(32))
    if profiles:
        config['COMPOSE_PROFILES'] = ','.join(profiles)
    output = args.output.resolve()
    fd, staging = tempfile.mkstemp(dir=output.parent, prefix='.bk-config-')
    try:
        with os.fdopen(fd, 'w', encoding='ascii', newline='\n') as stream:
            for key, value in config.items():
                print(f'{key}={value}', file=stream)
        os.link(staging, output)
    except FileExistsError:
        parser.exit(1, 'Settings already exist. Use the existing file to restart this instance.\n')
    finally:
        os.unlink(staging)
    print(f'Saved private settings to {output}. Sign in at {origin} as admin using PIGEON_INITIAL_PASSWORD.')


if __name__ == '__main__':
    main()
