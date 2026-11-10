"""Read-only cloud forwarding probe, executed inside the PIGEON container.

Example: python3 check_bk_forwarding.py DEVICE_DOMAIN --samples 120
Reports heartbeat freshness separately from real authenticated RPC availability.
No restart or recovery side effects; JSON lines contain no credentials/images.
"""
import argparse
from collections import namedtuple
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import statistics
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('domain')
    parser.add_argument('--samples', type=int, default=12)
    parser.add_argument('--interval', type=float, default=5)
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', args.domain):
        parser.error('Invalid device domain')
    if not 1 <= args.samples <= 720 or not 2 <= args.interval <= 60:
        parser.error('Use 1..720 samples and 2..60 second intervals')
    import grpc
    import server  # Loads the deployed protocol descriptors.
    from lamda.client import Device
    from executor.models import Device as Model, session_scope

    class Deadline(grpc.UnaryUnaryClientInterceptor):
        def intercept_unary_unary(self, continuation, details, request):
            Detail = namedtuple('Detail', 'method timeout metadata credentials wait_for_ready compression')
            return continuation(Detail(details.method, 4, details.metadata,
                                       details.credentials, False, None), request)

    cert = Path('/user/certificates') / (args.domain + '.pem')
    if not cert.is_file():
        parser.error('Device certificate is unavailable')
    good = []
    for index in range(args.samples):
        started = time.monotonic()
        row = dict(utc=datetime.now(timezone.utc).isoformat(), sample=index + 1)
        with session_scope() as session:
            device = session.query(Model).filter(Model.domain == args.domain,
                                                Model.is_deleted == False).one()
            value = device.last_heartbeat_time
            last = value.timestamp() if isinstance(value, datetime) else float(value or 0)
            row['heartbeat_age_s'] = round(time.time() - last, 2)
        client = Device('127.0.0.1', port=int(os.environ['API_PORT']), certificate=str(cert))
        client.channel = grpc.intercept_channel(client.channel, Deadline())
        try:
            client.device_info()
            row.update(rpc='ok', cloud_probe_ms=round((time.monotonic() - started) * 1000))
            good.append(row['cloud_probe_ms'])
        except Exception as error:
            row.update(rpc='error', error=str(error.code()) if hasattr(error, 'code') else type(error).__name__)
        finally:
            client.channel.close()
        print(json.dumps(row), flush=True)
        if index + 1 < args.samples:
            time.sleep(max(0, args.interval - (time.monotonic() - started)))
    print(json.dumps(dict(summary=True, samples=args.samples, ok=len(good),
                          errors=args.samples - len(good),
                          median_ms=statistics.median(good) if good else None)), flush=True)
    return 0 if len(good) == args.samples else 2


if __name__ == '__main__':
    raise SystemExit(main())
