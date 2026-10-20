from dataclasses import dataclass, replace, asdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
import sqlite3
import time

MARKER = 'calendar-sync-v12:'

@dataclass(frozen=True)
class Event:
    id: str
    start: datetime
    end: datetime
    title: str = ''
    body: str = ''
    all_day: bool = False
    declined: bool = False
    cancelled: bool = False
    marker: str = ''
    busy: bool = True
    private: bool = False
    reminders: bool = False
    visibility: str = 'default'
    location: str = ''
    has_attendees: bool = False
    has_attachments: bool = False

    def __post_init__(self):
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError('Timezone-aware times required')
        if self.end.astimezone(timezone.utc) <= self.start.astimezone(timezone.utc):
            raise ValueError('Invalid interval')

    def payload(self):
        return {k: v.isoformat() if isinstance(v, datetime) else v
                for k, v in asdict(self).items() if k != 'id'}

@dataclass
class Snapshot:
    events: list[Event]
    complete: bool = True

@dataclass
class Route:
    name: str
    calendar: str
    source: object
    target: object
    google_to_outlook: bool
    heart: bool = False

@dataclass
class Operation:
    action: str
    key: str
    target_id: str = ''
    event: Event | None = None
    source_end: str = ''


def identity(route, event):
    raw = json.dumps([route.name, route.calendar, event.id], ensure_ascii=True)
    return MARKER + hashlib.sha256(raw.encode()).hexdigest()


def transform(route, event, key):
    buffer = timedelta(minutes=15) if route.google_to_outlook and not event.all_day else timedelta()
    start = (event.start.astimezone(timezone.utc)-buffer).astimezone(event.start.tzinfo)
    end = (event.end.astimezone(timezone.utc)+buffer).astimezone(event.end.tzinfo)
    return Event('', start, end,
                 ('Больничка ❤️' if route.heart else 'Больничка') if route.google_to_outlook else '(T-Bank) '+event.title,
                 '' if route.google_to_outlook else event.body, event.all_day,
                 marker=key, busy=True, private=route.google_to_outlook, reminders=False, visibility='private' if route.google_to_outlook else 'default')


class State:
    def __init__(self, path=':memory:', readonly=False):
        self.db = sqlite3.connect(f'file:{path}?mode=ro', uri=True) if readonly else sqlite3.connect(path)
        if not readonly:
            self.db.execute('CREATE TABLE IF NOT EXISTS links (key TEXT PRIMARY KEY, route TEXT, target TEXT, source_end TEXT)')
            self.db.commit()
    def rows(self, route):
        return {k: (target, end) for k, target, end in self.db.execute('SELECT key,target,source_end FROM links WHERE route=?', (route,))}
    def save(self, route, op, target):
        if op.action != 'DELETE' and self.db.execute('SELECT route,target,source_end FROM links WHERE key=?', (op.key,)).fetchone() == (route, target, op.source_end):
            return
        if op.action == 'DELETE':
            self.db.execute('DELETE FROM links WHERE key=?', (op.key,))
        else:
            self.db.execute('INSERT OR REPLACE INTO links VALUES (?,?,?,?)', (op.key, route, target, op.source_end))
        self.db.commit()


def equivalent(left, right):
    a, b = left.payload(), right.payload()
    for name in ('start', 'end'):
        a[name] = getattr(left, name).astimezone(timezone.utc)
        b[name] = getattr(right, name).astimezone(timezone.utc)
    return a == b


def plan(route, state, now, days=30, allow_ids=None):
    source = route.source.read(now, now+timedelta(days=days))
    # Unbounded marker inventory is mandatory for crash/state-loss recovery.
    inventory = route.target.copies()
    if not inventory.complete:
        raise RuntimeError('Incomplete destination inventory; writes blocked')
    copies = {}
    for event in inventory.events:
        if event.marker:
            copies.setdefault(event.marker, []).append(event)
    rows = state.rows(route.name)
    desired = {}
    for event in source.events:
        if event.marker or (allow_ids is not None and event.id not in allow_ids):
            continue
        key = identity(route, event)
        if key in desired:
            raise RuntimeError('Duplicate source identity')
        if not event.declined and not event.cancelled and event.end > now and event.start < now+timedelta(days=days):
            desired[key] = event
    ops = []
    for key, event in desired.items():
        existing = copies.get(key, [])
        if len(existing) > 1:
            raise RuntimeError('Duplicate markers; manual reconciliation required')
        wanted = transform(route, event, key)
        action = 'CREATE' if not existing else 'SKIP' if equivalent(existing[0], wanted) else 'UPDATE'
        ops.append(Operation(action, key, existing[0].id if existing else '', wanted, event.end.isoformat()))
    # Only known links may be removed. Unmapped orphan markers are preserved.
    if source.complete:
        from types import SimpleNamespace
        selected_keys = None if allow_ids is None else {identity(route, SimpleNamespace(id=i)) for i in allow_ids}
        for key, (target, end) in rows.items():
            if key not in desired and datetime.fromisoformat(end) > now and (selected_keys is None or key in selected_keys):
                matches = copies.get(key, [])
                if len(matches) > 1:
                    raise RuntimeError('Duplicate markers')
                ops.append(Operation('DELETE', key, matches[0].id if matches else target))
    return ops


def apply(route, state, ops, max_deletes=5, confirm_deletes=False, attempts=3, sleeper=time.sleep, log=lambda *args: None):
    if sum(op.action == 'DELETE' for op in ops) > max_deletes and not confirm_deletes:
        raise RuntimeError('Deletion threshold exceeded')
    failures = 0
    for op in ops:
        try:
            target = op.target_id
            for attempt in range(attempts):
                try:
                    if op.action == 'CREATE':
                        if attempt == 0:
                            # plan() already took a complete destination
                            # inventory. Avoid an O(events) COM scan before
                            # every ordinary create. Reconcile only after an
                            # ambiguous/transient failed attempt.
                            target = route.target.create(op.event)
                        else:
                            found = route.target.copies()
                            if not found.complete:
                                raise RuntimeError('Incomplete reconciliation')
                            matches = [e for e in found.events if e.marker == op.key]
                            if len(matches) > 1:
                                raise RuntimeError('Duplicate markers')
                            target = matches[0].id if matches else route.target.create(op.event)
                    elif op.action == 'UPDATE':
                        route.target.update(target, op.event)
                    elif op.action == 'DELETE':
                        inventory = route.target.copies()
                        if not inventory.complete:
                            raise RuntimeError('Incomplete deletion reconciliation')
                        matches = [e for e in inventory.events if e.marker == op.key]
                        if len(matches) > 1:
                            raise RuntimeError('Duplicate markers')
                        if matches:
                            route.target.delete(matches[0].id)
                    break
                except Exception as error:
                    status = getattr(getattr(error, 'resp', None), 'status', None)
                    transient = isinstance(error, (TimeoutError, ConnectionError)) or status in (408, 429, 500, 502, 503, 504) or getattr(error, 'hresult', None) in (-2147418111, -2147417846, -2147023174)
                    if not transient or attempt+1 == attempts:
                        raise
                    sleeper(2**attempt)
            state.save(route.name, op, target)
            log(route.name, op.key, op.action, 'ok')
        except Exception as error:
            failures += 1
            # Do not log exception text: providers may embed event bodies or secrets.
            log(route.name, op.key, op.action, type(error).__name__)
    return failures
