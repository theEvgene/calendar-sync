from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
now = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=1)
for name in ('outlook', 'google', 'shared'):
    path = Path('local') / ('fake-'+name+'.json')
    path.parent.mkdir(exist_ok=True)
    if path.exists():
        raise SystemExit('Refusing to overwrite existing demo calendars')
    path.write_text(json.dumps([dict(id='synthetic-'+name, start=now.isoformat(), end=(now+timedelta(hours=1)).isoformat(), title='Synthetic demo', body='Synthetic plain text')]), encoding='utf-8')
print('Synthetic calendars ready')
