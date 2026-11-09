"""Read-only Android telemetry. Unknown values remain None, including battery."""
import re
import math

READ_COMMAND = """printf '__BK_BATTERY__\\n'; dumpsys battery
printf '__BK_STORAGE__\\n'; df -k /data
printf '__BK_DISPLAY__\\n'; wm size; wm density
printf '__BK_SYSTEM__\\n'; getprop ro.product.manufacturer; getprop ro.product.model; getprop ro.build.version.release; getprop ro.build.version.sdk; getprop ro.product.cpu.abi
"""


def parse_metrics(text):
    if isinstance(text, bytes):
        text = text.decode('utf-8', errors='replace')
    sections = re.split(r'__BK_(BATTERY|STORAGE|DISPLAY|SYSTEM)__\s*\n', text)
    parts = dict(zip(sections[1::2], sections[2::2]))
    battery = dict(re.findall(r'^\s*([A-Za-z ]+):\s*(.+?)\s*$', parts.get('BATTERY', ''), re.M))

    def number(key):
        try:
            value = float(battery[key])
            return value if math.isfinite(value) else None
        except (KeyError, TypeError, ValueError):
            return None

    level, scale = number('level'), number('scale')
    percent = round(level / scale * 100) if level is not None and scale and 0 <= level <= scale else None
    temperature, voltage = number('temperature'), number('voltage')
    powered = [battery.get(key) for key in ('AC powered', 'USB powered', 'Wireless powered', 'Dock powered')]
    charging = True if 'true' in powered else False if any(x == 'false' for x in powered) else None
    storage = re.search(r'^\S+\s+(\d+)\s+(\d+)\s+(\d+)\s+\d+%\s+/data\s*$', parts.get('STORAGE', ''), re.M)
    display = parts.get('DISPLAY', '')
    sizes = re.findall(r'(?:Physical|Override) size:\s*(\d+)x(\d+)', display)
    densities = re.findall(r'(?:Physical|Override) density:\s*(\d+)', display)
    system = parts.get('SYSTEM', '').strip().splitlines()
    return {
        'battery': {'percent': percent, 'charging': charging, 'status': number('status'),
                    'health': number('health'), 'temperature_c': temperature / 10 if temperature is not None else None,
                    'voltage_v': voltage / 1000 if voltage is not None else None,
                    'technology': battery.get('technology'),
                    'source': next((key for key in ('AC', 'USB', 'Wireless', 'Dock') if battery.get(key + ' powered') == 'true'),
                                   'battery' if charging is False else None)},
        'storage': dict(zip(('total', 'used', 'available'), (int(n) * 1024 for n in storage.groups()))) if storage else {},
        'display': {'width': int(sizes[-1][0]) if sizes else None, 'height': int(sizes[-1][1]) if sizes else None,
                    'density': int(densities[-1]) if densities else None},
        'system': dict(zip(('manufacturer', 'model', 'android', 'sdk', 'abi'), system[:5])),
    }
