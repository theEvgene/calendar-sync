"""Build a clean allowlisted ZIP and run tests from a Unicode/space path."""
from pathlib import Path
import json
import os
import re
import subprocess
import sys
import tempfile
import zipfile

root = Path(__file__).resolve().parents[1]
files = [p for p in root.iterdir() if p.is_file() and p.name not in {'config.json'} and p.suffix in {'.md', '.toml', '.txt', '.json'}]
files += [root / '.gitignore']
for directory in ('calendar_sync', 'tests', 'tools', 'fixtures'):
    files += list((root / directory).rglob('*.py'))
for path in files:
    content = path.read_text(encoding='utf-8')
    assert not content.startswith('\ufeff'), f'BOM: {path.name}'
    assert not re.search(r'[A-Za-z]:[\\/]Users[\\/][A-Za-z0-9_\-]+', content), path.name
    assert not re.search(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----', content), path.name
    assert not re.search(r'"(?:access_token|refresh_token|client_secret)"\s*:\s*"[^"\s]+"', content), path.name
with tempfile.TemporaryDirectory(prefix='calendar portable ') as temporary:
    destination = Path(temporary) / 'перенос с пробелами'
    destination.mkdir()
    archive = Path(temporary) / 'calendar-sync-stage-a.zip'
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as output:
        for path in sorted(files):
            output.write(path, path.relative_to(root).as_posix())
    with zipfile.ZipFile(archive) as source:
        source.extractall(destination)
    environment = dict(os.environ)
    environment.pop('PYTHONPATH', None)
    result = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests'], cwd=destination, env=environment, capture_output=True, text=True, encoding='utf-8')
    if result.returncode:
        print(result.stdout)
        print(result.stderr)
        raise SystemExit(result.returncode)
    (root / 'local').mkdir(exist_ok=True)
    (root / 'local' / archive.name).write_bytes(archive.read_bytes())
    print(json.dumps({'portable_files': len(files), 'unicode_space_path_tests': 'PASS', 'absolute_user_paths': 0, 'secret_patterns': 0, 'archive': 'local/'+archive.name}))
