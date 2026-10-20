"""Local CLI. No network imports or COM activation in fake mode."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sqlite3
import sys
from .core import State, Route, plan, apply
from .adapters import FakeAdapter, GoogleAdapter, OutlookAdapter


@contextmanager
def lock(path):
    import os
    path.parent.mkdir(parents=True, exist_ok=True)
    # OS advisory locks release automatically on crash; file content is irrelevant.
    handle = path.open('a+b')
    handle.seek(0, 2)
    if handle.tell() == 0:
        handle.write(b'0')
        handle.flush()
    handle.seek(0)
    try:
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise RuntimeError('Another run holds the lock') from None
    try:
        yield
    finally:
        handle.close()


def local(base, value):
    path = Path(value)
    if path.is_absolute() or '..' in path.parts:
        raise ValueError('Config paths must be relative without parent traversal')
    return base / path


def error_summary(error):
    """Report a safe error code without exposing provider text or event data."""
    result = {'error': type(error).__name__}
    hresult = getattr(error, 'hresult', None)
    if isinstance(hresult, int):
        result['hresult'] = hresult
    return result


def validate(config):
    if config.get('mode') not in ('fake', 'real'):
        raise ValueError('Mode must be fake or real')
    for flag in ('policy_approved', 'stage_b_accepted'):
        if type(config.get(flag, False)) is not bool:
            raise ValueError('Approval flags must be booleans')
    if type(config.get('max_deletes', 5)) is not int or config.get('max_deletes', 5) < 0:
        raise ValueError('Deletion threshold must be a nonnegative integer')
    if config['mode'] == 'real' and config.get('google_calendar') != 'primary':
        raise ValueError('Only own primary Google destination is allowed')
    for field in ('state_file', 'lock_file', 'log_file'):
        local(Path('.'), config[field])
    if config['mode'] == 'real':
        for field in ('token_file', 'oauth_client_file'):
            local(Path('.'), config[field])
    if len({config[k] for k in ('state_file', 'lock_file', 'log_file')}) != 3:
        raise ValueError('State, lock, and log paths must differ')


def authorize_google(config, base):
    if config['mode'] != 'real':
        raise RuntimeError('Google authorization requires real mode')
    client = local(base, config['oauth_client_file'])
    token = local(base, config['token_file'])
    if not client.is_file():
        raise FileNotFoundError('OAuth desktop client JSON is missing')
    if token.exists():
        raise RuntimeError('OAuth token already exists; refusing to overwrite it')
    from google_auth_oauthlib.flow import InstalledAppFlow
    scopes = ('https://www.googleapis.com/auth/calendar.events.owned',
              'https://www.googleapis.com/auth/calendar.events.readonly')
    credentials = InstalledAppFlow.from_client_secrets_file(str(client), scopes).run_local_server(port=0)
    token.parent.mkdir(parents=True, exist_ok=True)
    token.write_text(credentials.to_json(), encoding='utf-8')
    print(json.dumps({'authorized': True, 'token_file': config['token_file']}))


def build(config, base):
    if config['mode'] == 'fake':
        calendars = {name: FakeAdapter(path=local(base, config['fake'][name])) for name in ('outlook', 'google', 'shared')}
    elif config['mode'] == 'real':
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build as google_build
        token = local(base, config['token_file'])
        credentials = Credentials.from_authorized_user_file(token)
        if not credentials.valid:
            if not credentials.refresh_token:
                raise RuntimeError('OAuth credentials require local provisioning')
            credentials.refresh(Request())
            # Refresh stays in memory; dry-run never writes credentials.
        service = google_build('calendar', 'v3', credentials=credentials, cache_discovery=False)
        calendars = dict(outlook=OutlookAdapter(config['timezone']),
                         google=GoogleAdapter(service, config['google_calendar'], config['timezone'], True),
                         shared=GoogleAdapter(service, config['shared_calendar'], config['timezone']))
        if config['google_calendar'] == config['shared_calendar']:
            raise ValueError('Source-only calendar must differ from own calendar')
    else:
        raise ValueError('Unknown mode')
    return [Route('outlook-to-google', 'outlook-default', calendars['outlook'], calendars['google'], False),
            Route('google-to-outlook', config.get('google_calendar', 'own'), calendars['google'], calendars['outlook'], True),
            Route('shared-to-outlook', config.get('shared_calendar', 'shared'), calendars['shared'], calendars['outlook'], True, True)]


def main(argv=None):
    parser = argparse.ArgumentParser(description='Calendar Sync v1.2; default is dry-run')
    parser.add_argument('command', nargs='?', choices=['dry-run', 'apply', 'diagnose', 'authorize-google'], default='dry-run')
    parser.add_argument('--config', default='config.json')
    parser.add_argument('--test-id', action='append', help='Exact normalized source ID; repeat for multiple events')
    parser.add_argument('--confirm-deletes', action='store_true')
    parser.add_argument('--confirm-initial-fill', action='store_true')
    args = parser.parse_args(argv)
    handler = None
    try:
        config_path = Path(args.config).resolve()
        config = json.loads(config_path.read_text(encoding='utf-8-sig'))
        validate(config)
        base = config_path.parent
        state_path = local(base, config['state_file'])
        if args.command == 'authorize-google':
            authorize_google(config, base)
            return 0
        if args.command == 'diagnose':
            print(json.dumps({'python': sys.version.split()[0], 'mode': config['mode'], 'state_exists': state_path.exists(),
                              'outlook_integration': 'UNVERIFIED', 'real_connections_attempted': False}))
            return 0
        if config['mode'] == 'real' and args.command == 'apply':
            if not config.get('policy_approved', False):
                raise RuntimeError('Corporate policy approval required')
            if not args.test_id and not (config.get('stage_b_accepted', False) and args.confirm_initial_fill):
                raise RuntimeError('Real writes require test IDs or accepted stage B and explicit full-fill confirmation')
        log = logging.getLogger('calendar_sync')
        log.setLevel(logging.INFO)
        if args.command == 'apply':
            log_path = local(base, config['log_file'])
            log_path.parent.mkdir(parents=True, exist_ok=True)
            handler = RotatingFileHandler(log_path, maxBytes=1_000_000, backupCount=3, encoding='utf-8')
            handler.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
            log.addHandler(handler)
        with lock(local(base, config['lock_file'])):
            # A memory copy of SQLite makes dry-run entirely read-only for persistent sync state.
            state = State()
            if state_path.exists():
                source = sqlite3.connect(f'file:{state_path.as_posix()}?mode=ro', uri=True)
                source.backup(state.db)
                source.close()
            if args.command == 'apply':
                state_path.parent.mkdir(parents=True, exist_ok=True)
                state.db.close()
                state = State(str(state_path))
            routes = build(config, base)
            now = datetime.now(timezone.utc)
            failures = 0
            for route in routes:
                try:
                    ops = plan(route, state, now, allow_ids=set(args.test_id) if args.test_id else None)
                    counts = {action: sum(o.action == action for o in ops) for action in ('CREATE', 'UPDATE', 'DELETE', 'SKIP')}
                    print(json.dumps({'route': route.name, **counts}))
                    if args.command == 'apply':
                        failures += apply(route, state, ops, config.get('max_deletes', 5), args.confirm_deletes,
                                          log=lambda *fields: log.info('%s %s %s %s', *fields))
                except Exception as error:
                    failures += 1
                    summary = error_summary(error)
                    print(json.dumps({'route': route.name, **summary}), file=sys.stderr)
                    log.error('%s %s', route.name, json.dumps(summary))
            state.db.close()
            return 1 if failures else 0
    except Exception as error:
        print('Blocked: '+json.dumps(error_summary(error)), file=sys.stderr)
        return 2
    finally:
        if handler is not None:
            logging.getLogger('calendar_sync').removeHandler(handler)
            handler.close()

if __name__ == '__main__':
    raise SystemExit(main())
