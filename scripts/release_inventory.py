"""Emit installed dependency versions and recorded license metadata."""
from importlib import metadata
import json
import platform

records = []
for distribution in sorted(metadata.distributions(), key=lambda d: d.metadata.get('Name', '').lower()):
    record = {
        'name': distribution.metadata.get('Name'),
        'version': distribution.version,
        'license_expression': distribution.metadata.get('License-Expression'),
        'license_recorded': distribution.metadata.get('License'),
    }
    if record['license_recorded'] and len(record['license_recorded']) > 300:
        record['license_recorded'] = 'See installed distribution metadata LICENSE text'
    records.append(record)

print(json.dumps({
    'python': platform.python_version(),
    'platform': platform.platform(),
    'scope': 'installed metadata inventory; no new redistribution authorization',
    'dependencies': records
}, indent=2))
