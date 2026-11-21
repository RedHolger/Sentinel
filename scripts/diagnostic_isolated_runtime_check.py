"""Diagnostic-only isolated verification script for ADI Runtime.

PURPOSE:
This script provides diagnostic evidence that the built adi-runtime wheel's bytecode
functions correctly when installed into site-packages and executed in source-isolated
conditions.

IMPORTANT:
This diagnostic probe does NOT satisfy the release acceptance gate.
The release verification gate (scripts/verify_portfolio_release.py) strictly requires
complete offline dependency resolution and successful pip check.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
clean_env = {k: v for k, v in os.environ.items() if not k.startswith(('ADI_', 'PYTHONPATH', 'VIRTUAL_ENV'))}

with tempfile.TemporaryDirectory(prefix='adi-diagnostic-') as temporary:
    work = Path(temporary)

    # 1. Relocate sources and build wheels
    for package in ['adi-common', 'adi-runtime']:
        target = work / package
        target.mkdir()
        for name in ['pyproject.toml', 'README.md']:
            shutil.copy2(root / package / name, target / name)
        for name in ['src', 'tests', 'schemas']:
            source = root / package / name
            if source.exists():
                shutil.copytree(source, target / name, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        subprocess.run(['uv', 'build', '--offline', '--wheel', '--out-dir', str(work / 'wheels'), str(target)], check=True, env=clean_env, cwd=work)

    # 2. Create clean virtual environment
    subprocess.run(['uv', 'venv', '--python', '3.11', str(work / 'env')], check=True, env=clean_env, cwd=work)
    python = work / 'env/bin/python'

    common_whl = next((work / 'wheels').glob('adi_common-*.whl'))
    runtime_whl = next((work / 'wheels').glob('adi_runtime-*.whl'))

    # Install adi-common with offline dependencies
    subprocess.run(['uv', 'pip', 'install', '--offline', '--python', str(python), str(common_whl)], check=True, env=clean_env, cwd=work)
    # Diagnostic install of adi-runtime with --no-deps
    subprocess.run(['uv', 'pip', 'install', '--offline', '--no-deps', '--python', str(python), str(runtime_whl)], check=True, env=clean_env, cwd=work)

    # 3. Remove relocated sources to guarantee site-packages execution
    shutil.rmtree(work / 'adi-common')
    shutil.rmtree(work / 'adi-runtime')

    exec_env = dict(clean_env)
    exec_env['PATH'] = f"{work / 'env/bin'}:{exec_env.get('PATH', '')}"

    # 4. In-process probe of installed wheel
    probe_code = '''
import math
import socket
import adi_runtime
from adi_runtime import (
    WindowFeatureError,
    extract_window_features,
    compute_channel_metrics,
    stable_mean,
    stable_rms,
    stable_peak_abs,
)

source = adi_runtime.__file__
assert 'site-packages' in source, f"Source not in site-packages: {source}"

def deny(*args, **kwargs):
    raise RuntimeError('Network forbidden')
socket.socket.connect = deny
socket.create_connection = deny

# 1. Opposite-sign extreme values
m_opp = compute_channel_metrics([1.5e308, -1.5e308])
assert m_opp["mean"] == 0.0, f"Expected 0.0, got {m_opp['mean']}"
assert m_opp["rms"] == 1.5e308
assert m_opp["peak_abs"] == 1.5e308

# 2. Public helper validation
try:
    compute_channel_metrics([True])
    raise AssertionError("Failed to reject boolean")
except WindowFeatureError:
    pass

# 3. Cancellation-sensitive [1e16, 1.0, -1e16]
m_canc = compute_channel_metrics([1e16, 1.0, -1e16])
assert math.isclose(m_canc["mean"], 1.0 / 3.0, rel_tol=1e-15)

# 4. Analytical reference [3.0, 4.0]
rec = {
    "schema_version": "1.0.0",
    "recording_id": "rec-diag-01",
    "machine_id": "asset-01",
    "session_id": "sess-01",
    "provenance": "synthetic",
    "sample_rate_hz": 1000.0,
    "acceleration_unit": "g",
    "channels": ["x"],
    "samples": [[3.0], [4.0]],
    "source_id": "diag-test",
    "source_sha256": "0" * 64,
}
res = extract_window_features(rec, window_size=2, hop_size=2)
assert res["status"] == "SUCCESS"
assert math.isclose(res["windows"][0]["features"]["x"]["mean"], 3.5)
assert math.isclose(res["windows"][0]["features"]["x"]["rms"], math.sqrt(12.5))
assert math.isclose(res["windows"][0]["features"]["x"]["peak_abs"], 4.0)

print("DIAGNOSTIC_PROBE_SUCCESS")
'''
    probe_res = subprocess.run([str(python), '-I', '-c', probe_code], capture_output=True, text=True, env=exec_env, cwd=work)
    if probe_res.returncode != 0:
        print(f"Diagnostic probe failed: code={probe_res.returncode}, stderr={probe_res.stderr}", file=sys.stderr)
        sys.exit(1)

    print(json.dumps({
        'diagnostic_artifact': True,
        'diagnostic_status': 'PASS',
        'source_in_site_packages': True,
        'opposite_extremes_mean_zero_verified': True,
        'helper_boolean_rejection_verified': True,
        'cancellation_sensitive_mean_verified': True,
        'analytical_reference_verified': True,
        'release_gate_satisfaction': 'CANNOT_SATISFY_RELEASE_ACCEPTANCE_GATE',
        'release_blocker_note': 'Missing offline numpy wheel prevents formal release gate passage in scripts/verify_portfolio_release.py'
    }, indent=2))
