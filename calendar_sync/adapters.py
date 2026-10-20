"""Adapters. Outlook COM integration is UNVERIFIED until stage B."""
from dataclasses import replace
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
from .core import Event, Snapshot, MARKER


def received_meeting_unconfirmed(item):
    """True only for an incoming meeting that the user did not accept."""
    return getattr(item, 'MeetingStatus', 0) == 3 and item.ResponseStatus != 3


def decode(data):
    data = dict(data)
    for field in ('start', 'end'):
        data[field] = datetime.fromisoformat(data[field])
    return Event(**data)


class FakeAdapter:
    def __init__(self, events=(), path=None):
        self.events = {e.id: e for e in events}
        self.complete = True
        self.writes = 0
        self.path = Path(path) if path else None
        if self.path and self.path.exists():
            self.events = {e.id: e for e in map(decode, json.loads(self.path.read_text(encoding='utf-8-sig')))}
    def read(self, start, end):
        return Snapshot([e for e in self.events.values() if e.end > start and e.start < end], self.complete)
    def copies(self):
        return Snapshot([e for e in self.events.values() if e.marker.startswith(MARKER)], self.complete)
    def persist(self):
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_suffix('.tmp')
            temp.write_text(json.dumps([dict(id=e.id, **e.payload()) for e in self.events.values()], ensure_ascii=False, indent=2), encoding='utf-8')
            temp.replace(self.path)
    def create(self, event):
        import uuid
        ident = uuid.uuid4().hex
        self.events[ident] = replace(event, id=ident)
        self.writes += 1
        self.persist()
        return ident
    def update(self, ident, event):
        if ident not in self.events:
            raise KeyError('Missing copy')
        self.events[ident] = replace(event, id=ident)
        self.writes += 1
        self.persist()
    def delete(self, ident):
        self.events.pop(ident, None)
        self.writes += 1
        self.persist()


class GoogleAdapter:
    def __init__(self, service, calendar_id, zone, writable=False):
        from zoneinfo import ZoneInfo
        self.service, self.calendar_id = service, calendar_id
        self.zone = ZoneInfo(zone)
        self.writable = writable
    def _list(self, **kwargs):
        result, token = [], None
        seen = set()
        while True:
            response = self.service.events().list(calendarId=self.calendar_id, pageToken=token, maxResults=2500, **kwargs).execute()
            result.extend(response.get('items', []))
            token = response.get('nextPageToken')
            if not token:
                return result
            if token in seen:
                raise RuntimeError('Repeated pagination token')
            seen.add(token)
    def _event(self, data):
        def stamp(value):
            from zoneinfo import ZoneInfo
            if 'date' in value:
                return datetime.fromisoformat(value['date']).replace(tzinfo=self.zone)
            value_dt = datetime.fromisoformat(value['dateTime'].replace('Z', '+00:00'))
            return value_dt if value_dt.tzinfo else value_dt.replace(tzinfo=ZoneInfo(value.get('timeZone', str(self.zone))))
        return Event(data['id'], stamp(data['start']), stamp(data['end']), data.get('summary', ''), data.get('description', ''),
                     'date' in data['start'], any(a.get('self') and a.get('responseStatus') == 'declined' for a in data.get('attendees', [])),
                     data.get('status') == 'cancelled', data.get('extendedProperties', {}).get('private', {}).get('calendarSyncKey', ''),
                     data.get('transparency', 'opaque') != 'transparent', data.get('visibility') == 'private',
                     data.get('reminders', {}).get('useDefault', True) or bool(data.get('reminders', {}).get('overrides', [])),
                     data.get('visibility', 'default'), data.get('location', ''), bool(data.get('attendees')), bool(data.get('attachments')))
    def read(self, start, end):
        data = self._list(timeMin=start.isoformat(), timeMax=end.isoformat(), singleEvents=True, showDeleted=False)
        return Snapshot([self._event(e) for e in data if 'start' in e and 'end' in e], all('start' in e and 'end' in e for e in data))
    def copies(self):
        # No time bound: copies may have been moved by hand or the state may be lost.
        data = self._list(privateExtendedProperty='calendarSyncOwner=v12', singleEvents=True, showDeleted=False)
        if any('start' not in e or 'end' not in e for e in data):
            return Snapshot([], False)
        return Snapshot([self._event(e) for e in data])
    def _body(self, event):
        def stamp(value):
            return {'date': value.date().isoformat()} if event.all_day else {'dateTime': value.isoformat()}
        return dict(summary=event.title, description=event.body, start=stamp(event.start), end=stamp(event.end),
                    location='', attendees=[], attachments=[], transparency='opaque', visibility='default', reminders={'useDefault': False, 'overrides': []},
                    extendedProperties={'private': {'calendarSyncOwner': 'v12', 'calendarSyncKey': event.marker}})
    def _check(self):
        if not self.writable:
            raise PermissionError('Read-only calendar')
    def create(self, event):
        self._check()
        # Deterministic API ID makes ambiguous retries idempotent, including eventual listing lag.
        import hashlib
        body = self._body(event)
        for generation in range(100):
            # Deleted Google IDs are tombstones and cannot be reused. Probe a
            # deterministic generation chain so retries recover ambiguous writes.
            body['id'] = hashlib.sha256((event.marker+':'+str(generation)).encode()).hexdigest()
            try:
                return self.service.events().insert(calendarId=self.calendar_id, body=body, sendUpdates='none').execute()['id']
            except Exception as error:
                if getattr(getattr(error, 'resp', None), 'status', None) != 409:
                    raise
                existing = self.service.events().get(calendarId=self.calendar_id, eventId=body['id']).execute()
                if existing.get('status') == 'cancelled':
                    continue
                if existing.get('extendedProperties', {}).get('private', {}).get('calendarSyncKey') != event.marker:
                    raise RuntimeError('ID collision') from None
                return existing['id']
        raise RuntimeError('Too many deleted generations; reconciliation required')
    def update(self, ident, event):
        self._check()
        existing = self.service.events().get(calendarId=self.calendar_id, eventId=ident).execute()
        if existing.get('extendedProperties', {}).get('private', {}).get('calendarSyncKey') != event.marker:
            raise RuntimeError('Marker mismatch')
        self.service.events().update(calendarId=self.calendar_id, eventId=ident, body=self._body(event), sendUpdates='none').execute()
    def delete(self, ident):
        self._check()
        try:
            existing = self.service.events().get(calendarId=self.calendar_id, eventId=ident).execute()
            if existing.get('status') == 'cancelled':
                return
            if not existing.get('extendedProperties', {}).get('private', {}).get('calendarSyncKey', '').startswith(MARKER):
                raise RuntimeError('Unmarked target; deletion blocked')
            self.service.events().delete(calendarId=self.calendar_id, eventId=ident, sendUpdates='none').execute()
        except Exception as error:
            if getattr(getattr(error, 'resp', None), 'status', None) not in (404, 410):
                raise


class OutlookAdapter:
    """Default calendar only; local COM session. Stage B verification REQUIRED."""
    property_name = 'CalendarSyncKey'
    def __init__(self, zone):
        import sys
        if sys.platform != 'win32':
            raise RuntimeError('Outlook COM requires Windows')
        import win32com.client
        from zoneinfo import ZoneInfo
        self.zone = ZoneInfo(zone)
        self.namespace = win32com.client.Dispatch('Outlook.Application').GetNamespace('MAPI')
        self.folder = self.namespace.GetDefaultFolder(9)
    def _marker(self, item):
        prop = item.UserProperties.Find(self.property_name)
        return prop.Value if prop else ''
    def _stamp(self, value, assume_utc=False):
        # On this Outlook profile StartUTC is exposed with the workstation
        # offset applied a second time, while Start carries the correct
        # timezone-aware value. Normalize the value itself and never add the
        # workstation offset again.
        value_zone = getattr(value, 'tzinfo', None)
        stamp = datetime(value.year, value.month, value.day, value.hour, value.minute, value.second,
                         tzinfo=value_zone or (timezone.utc if assume_utc else self.zone))
        return stamp.astimezone(timezone.utc)
    def _event(self, item, original=None):
        try:
            candidate_start = item.Start
        except Exception:
            candidate_start = None
        try:
            candidate_end = item.End
        except Exception:
            candidate_end = None
        has_start = isinstance(candidate_start, datetime)
        has_end = isinstance(candidate_end, datetime)
        raw_start = candidate_start if has_start else item.StartUTC
        raw_end = candidate_end if has_end else item.EndUTC
        start, end = self._stamp(raw_start, assume_utc=not has_start), self._stamp(raw_end, assume_utc=not has_end)
        if item.AllDayEvent:
            start, end = start.astimezone(self.zone), end.astimezone(self.zone)
        ident = str(item.GlobalAppointmentID)
        if item.IsRecurring:
            original = original or start
            ident += ':' + original.astimezone(timezone.utc).isoformat()
        # A received meeting must be explicitly accepted before it is copied.
        # Ordinary appointments and meetings we own have no response to give
        # and remain eligible.  Outlook constants: MeetingStatus=3 means
        # received meeting; ResponseStatus=3 means accepted.
        received_unconfirmed = received_meeting_unconfirmed(item)
        return Event(ident, start, end, item.Subject or '', item.Body or '', bool(item.AllDayEvent),
                     received_unconfirmed, False, self._marker(item), item.BusyStatus == 2,
                     item.Sensitivity == 2, bool(item.ReminderSet), 'private' if item.Sensitivity == 2 else 'default',
                     item.Location or '', bool(item.Recipients.Count), bool(item.Attachments.Count))
    def read(self, start, end):
        # Sort then IncludeRecurrences then Restrict is required for COM expansion.
        items = self.folder.Items
        items.Sort('[Start]')
        items.IncludeRecurrences = True
        local_start = start.astimezone(self.zone).strftime('%m/%d/%Y %I:%M %p')
        local_end = end.astimezone(self.zone).strftime('%m/%d/%Y %I:%M %p')
        items = items.Restrict(f"[Start] < '{local_end}' AND [End] > '{local_start}'")
        events = []
        for item in items:
            if item.Class != 26:
                continue
            original = None
            if item.IsRecurring:
                pattern = item.GetRecurrencePattern()
                for index in range(1, pattern.Exceptions.Count+1):
                    exception = pattern.Exceptions.Item(index)
                    try:
                        exception_start = getattr(exception.AppointmentItem, 'Start', None)
                        if not isinstance(exception_start, datetime):
                            exception_start = exception.AppointmentItem.StartUTC
                        item_start = getattr(item, 'Start', None)
                        if not isinstance(item_start, datetime):
                            item_start = item.StartUTC
                        if not exception.Deleted and exception_start == item_start:
                            value = exception.OriginalDate
                            original = datetime(value.year, value.month, value.day, value.hour, value.minute, value.second, tzinfo=self.zone)
                            break
                    except Exception:
                        # A malformed exception must not turn the whole COM
                        # snapshot into an empty source; it simply has no
                        # provable OriginalDate for this expanded item.
                        continue
            events.append(self._event(item, original))
        return Snapshot(events)
    def copies(self):
        events = []
        for item in self.folder.Items:
            if item.Class == 26 and self._marker(item).startswith(MARKER):
                event = self._event(item)
                events.append(replace(event, id=item.EntryID))
        return Snapshot(events)
    def _write(self, item, event):
        item.Subject = event.title
        item.Body = ''
        item.AllDayEvent = event.all_day
        if event.all_day:
            item.Start = event.start.astimezone(self.zone).replace(tzinfo=None)
            item.End = event.end.astimezone(self.zone).replace(tzinfo=None)
        else:
            # Outlook's COM setter for StartUTC applies the workstation zone
            # again on some profiles.  Write local Start/End in the configured
            # Outlook zone; StartUTC/EndUTC are then read back as true UTC.
            item.Start = event.start.astimezone(self.zone).replace(tzinfo=None)
            item.End = event.end.astimezone(self.zone).replace(tzinfo=None)
        item.BusyStatus, item.Sensitivity, item.ReminderSet = 2, 2, False
        item.Location = ''
        for index in range(item.Recipients.Count, 0, -1):
            item.Recipients.Remove(index)
        for index in range(item.Attachments.Count, 0, -1):
            item.Attachments.Remove(index)
        prop = item.UserProperties.Find(self.property_name) or item.UserProperties.Add(self.property_name, 1)
        prop.Value = event.marker
        item.Save()  # Never Send(): no meeting recipients or invitations are created.
        return item.EntryID
    def create(self, event):
        return self._write(self.folder.Items.Add(1), event)
    def update(self, ident, event):
        item = self.namespace.GetItemFromID(ident, self.folder.StoreID)
        if self._marker(item) != event.marker:
            raise RuntimeError('Marker mismatch')
        self._write(item, event)
    def delete(self, ident):
        # Enumerate by EntryID first: missing copy is already deleted.
        for event in self.copies().events:
            if event.id == ident:
                self.namespace.GetItemFromID(ident, self.folder.StoreID).Delete()
                return
