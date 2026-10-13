import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).parents[1]


@pytest.mark.skipif(os.name == 'nt' or not shutil.which('bash'), reason='Linux CI scripts')
@pytest.mark.parametrize('gate_exit', [0, 1])
def test_security_reports_include_unfixed_and_gate_failure_is_preserved(tmp_path, gate_exit):
    binary = tmp_path / 'bin'
    binary.mkdir()
    calls = tmp_path / 'calls.jsonl'
    tool = '''#!/usr/bin/env python3
import json, os, pathlib, sys
args = sys.argv[1:]
with open(os.environ['CALLS'], 'a') as log:
    log.write(json.dumps([pathlib.Path(sys.argv[0]).name, args]) + '\\n')
if pathlib.Path(sys.argv[0]).name == 'trivy':
    pathlib.Path(args[args.index('--output')+1]).write_text('test report')
    if args[args.index('--exit-code')+1] == '1':
        sys.exit(int(os.environ['GATE_EXIT']))
'''
    for name in ('docker', 'trivy'):
        path = binary / name
        path.write_text(tool)
        path.chmod(0o755)
    env = dict(os.environ, PATH=f"{binary}:{os.environ['PATH']}", CALLS=str(calls),
               GATE_EXIT=str(gate_exit), SECURITY_REPORT_DIR=str(tmp_path / 'reports'))
    git = binary / 'git'
    git.write_text("#!/bin/sh\nprintf '%s\\n' test-commit\n")
    git.chmod(0o755)
    result = subprocess.run(['bash', str(ROOT / 'scripts/security-scan.sh')], env=env, capture_output=True, text=True)
    assert result.returncode == gate_exit, result.stderr
    entries = [json.loads(line) for line in calls.read_text().splitlines()]
    assert entries[0][0] == 'docker'
    scans = [args for name, args in entries if name == 'trivy']
    assert len(scans) == 2
    for scan in scans:
        assert '--ignore-unfixed=false' in scan
        assert scan[scan.index('--severity')+1] == 'CRITICAL,HIGH'
    assert [scan[scan.index('--exit-code')+1] for scan in scans] == ['0', '1']
    assert len(list((tmp_path / 'reports').glob('*.txt'))) == 2
