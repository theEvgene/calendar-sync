import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest.mock import Mock, patch
from calendar_sync.core import Event, Snapshot, Route, State, plan, apply, identity, transform
from calendar_sync.adapters import FakeAdapter, GoogleAdapter
from calendar_sync.__main__ import main, lock, authorize_google
from calendar_sync.adapters import received_meeting_unconfirmed

NOW = datetime(2030, 3, 20, tzinfo=timezone.utc)

class SyncTests(unittest.TestCase):
    def test_received_outlook_meeting_requires_explicit_acceptance(self):
        class Item:
            MeetingStatus = 3
            ResponseStatus = 2
        self.assertTrue(received_meeting_unconfirmed(Item()))
        Item.ResponseStatus = 3
        self.assertFalse(received_meeting_unconfirmed(Item()))

    def test_personal_outlook_appointment_has_no_response_requirement(self):
        class Item:
            MeetingStatus = 0
            ResponseStatus = 0
        self.assertFalse(received_meeting_unconfirmed(Item()))

    def setUp(self):
        self.event = Event('original', NOW+timedelta(days=1), NOW+timedelta(days=1,hours=1), 'Meeting', 'Plain\nBody')
        self.source = FakeAdapter([self.event])
        self.target = FakeAdapter()
        self.route = Route('g2o','own',self.source,self.target,True)
        self.state = State()
    def tearDown(self):
        self.state.db.close()
    def run_sync(self, **kwargs):
        ops = plan(self.route,self.state,NOW,**kwargs)
        self.assertEqual(apply(self.route,self.state,ops,sleeper=lambda _: None),0)
        return ops

    def test_google_authorization_requires_real_mode(self):
        with self.assertRaisesRegex(RuntimeError, 'real mode'):
            authorize_google({'mode': 'fake'}, Path('.'))

    def test_google_authorization_requires_client_file(self):
        config = {'mode': 'real', 'oauth_client_file': 'missing-client.json',
                  'token_file': 'token.json'}
        with self.assertRaisesRegex(FileNotFoundError, 'client JSON'):
            authorize_google(config, Path('.'))
    def test_rules_both_directions_and_shared(self):
        key=identity(self.route,self.event)
        own=transform(self.route,self.event,key)
        self.assertEqual((own.title,own.body,own.start,own.end,own.private,own.busy,own.reminders),('Больничка','',self.event.start-timedelta(minutes=15),self.event.end+timedelta(minutes=15),True,True,False))
        shared=transform(replace(self.route,heart=True),self.event,key)
        self.assertEqual(shared.title,'Больничка ❤️')
        other=transform(replace(self.route,google_to_outlook=False),self.event,key)
        self.assertEqual((other.title,other.body,other.start,other.private),('(T-Bank) Meeting','Plain\nBody',self.event.start,False))
    def test_all_day_no_buffer(self):
        event=replace(self.event,all_day=True,end=self.event.start+timedelta(days=1))
        out=transform(self.route,event,'key')
        self.assertEqual((out.start,out.end,out.all_day),(event.start,event.end,True))
    def test_idempotent_and_manual_edit_delete(self):
        self.run_sync()
        writes=self.target.writes
        self.assertEqual([o.action for o in self.run_sync()],['SKIP'])
        self.assertEqual(self.target.writes,writes)
        ident=next(iter(self.target.events))
        self.target.events[ident]=replace(self.target.events[ident],title='tampered',busy=False)
        self.assertEqual([o.action for o in self.run_sync()],['UPDATE'])
        self.target.events.clear()
        self.assertEqual([o.action for o in self.run_sync()],['CREATE'])
    def test_decline_cancel_delete(self):
        for flag in ('declined','cancelled'):
            with self.subTest(flag=flag):
                self.source.events[self.event.id]=self.event
                self.run_sync()
                self.source.events[self.event.id]=replace(self.event,**{flag:True})
                self.assertEqual([o.action for o in self.run_sync()],['DELETE'])
                self.assertFalse(self.target.events)
        self.source.events[self.event.id]=self.event
        self.run_sync()
        self.source.events.clear()
        self.assertEqual([o.action for o in self.run_sync()],['DELETE'])
    def test_window_move_return_and_history(self):
        self.run_sync()
        self.source.events[self.event.id]=replace(self.event,start=NOW+timedelta(days=40),end=NOW+timedelta(days=40,hours=1))
        self.assertEqual([o.action for o in self.run_sync()],['DELETE'])
        self.source.events[self.event.id]=self.event
        self.assertEqual([o.action for o in self.run_sync()],['CREATE'])
        self.source.events.clear()
        later=NOW+timedelta(days=2)
        self.assertEqual(plan(self.route,self.state,later),[])
        self.assertEqual(len(self.target.events),1)
    def test_no_initial_past_import(self):
        self.source.events={'past':replace(self.event,id='past',start=NOW-timedelta(days=2),end=NOW-timedelta(days=1))}
        self.assertEqual(self.run_sync(),[])
    def test_incomplete_source_and_destination(self):
        self.run_sync()
        self.source.events.clear()
        self.source.complete=False
        self.assertEqual(self.run_sync(),[])
        self.target.complete=False
        with self.assertRaises(RuntimeError):
            self.run_sync()
    def test_crash_after_create_and_state_loss(self):
        ops=plan(self.route,self.state,NOW)
        self.target.create(ops[0].event)
        self.assertEqual([o.action for o in self.run_sync()],['SKIP'])
        self.state.db.execute('DELETE FROM links')
        self.state.db.commit()
        self.run_sync()
        self.assertEqual(len(self.target.events),1)
        self.assertEqual(len(self.state.rows(self.route.name)),1)
    def test_ambiguous_create_timeout(self):
        original=self.target.create
        def create(event):
            original(event)
            raise TimeoutError()
        self.target.create=create
        self.run_sync()
        self.assertEqual(len(self.target.events),1)
    def test_duplicates_block_and_titles_do_not_identify(self):
        self.target.events={'ordinary':replace(self.event,id='ordinary',title='Больничка')}
        self.run_sync()
        self.assertEqual(len(self.target.events),2)
        copy=next(e for e in self.target.events.values() if e.marker)
        self.target.events['duplicate']=replace(copy,id='duplicate')
        with self.assertRaises(RuntimeError):
            self.run_sync()
    def test_series_exceptions_overlaps_and_identity(self):
        # Adapters supply expanded occurrences; IDs retain original occurrence identity on move.
        self.source.events={e.id:e for e in [replace(self.event,id='series:original-1'),replace(self.event,id='series:original-2'),replace(self.event,id='overlap')]}
        self.run_sync()
        self.assertEqual(len(self.target.events),3)
        self.source.events['series:original-1']=replace(self.event,id='series:original-1',start=self.event.start+timedelta(hours=3),end=self.event.end+timedelta(hours=3))
        del self.source.events['series:original-2']
        ops=self.run_sync()
        self.assertEqual(sorted(o.action for o in ops),['DELETE','SKIP','UPDATE'])
        self.assertEqual(len(self.target.events),2)
    def test_three_routes_no_cycles(self):
        outlook=FakeAdapter([replace(self.event,id='outlook')])
        shared=FakeAdapter([replace(self.event,id='shared')])
        routes=[self.route,Route('o2g','default',outlook,self.source,False),Route('shared','shared',shared,outlook,True,True)]
        # Connect first route to the same Outlook destination.
        routes[0]=replace(self.route,target=outlook)
        for _ in range(3):
            for route in routes:
                self.assertEqual(apply(route,self.state,plan(route,self.state,NOW)),0)
        self.assertEqual((len(outlook.events),len(self.source.events),len(shared.events)),(3,2,1))
        before=(outlook.writes,self.source.writes,shared.writes)
        for route in routes:
            apply(route,self.state,plan(route,self.state,NOW))
        self.assertEqual(before,(outlook.writes,self.source.writes,shared.writes))
    def test_delete_threshold_and_test_scope(self):
        self.source.events={str(i):replace(self.event,id=str(i)) for i in range(7)}
        self.run_sync()
        self.source.events.clear()
        ops=plan(self.route,self.state,NOW)
        with self.assertRaises(RuntimeError):
            apply(self.route,self.state,ops,max_deletes=2)
        self.assertEqual(len(self.target.events),7)
        self.assertEqual([o.action for o in self.run_sync(allow_ids={'0'})],['DELETE'])
        self.assertEqual(len(self.target.events),6)
        self.assertEqual(apply(self.route,self.state,plan(self.route,self.state,NOW),confirm_deletes=True),0)
    def test_failure_one_event_continues(self):
        self.source.events['second']=replace(self.event,id='second')
        create=self.target.create
        def sometimes(event):
            if event.marker==identity(self.route,self.event):
                raise ValueError('Sensitive error text')
            return create(event)
        self.target.create=sometimes
        messages=[]
        self.assertEqual(apply(self.route,self.state,plan(self.route,self.state,NOW),log=lambda *args: messages.append(args)),1)
        self.assertEqual(len(self.target.events),1)
        self.assertNotIn('Sensitive',str(messages))
    def test_timezone_equal_instant_no_update(self):
        self.run_sync()
        ident=next(iter(self.target.events))
        event=self.target.events[ident]
        zone=timezone(timedelta(hours=3))
        self.target.events[ident]=replace(event,start=event.start.astimezone(zone),end=event.end.astimezone(zone))
        self.assertEqual([o.action for o in self.run_sync()],['SKIP'])
    def test_naive_time_rejected(self):
        with self.assertRaises(ValueError):
            replace(self.event,start=datetime(2030,1,1))

class AdapterTests(unittest.TestCase):
    def test_google_pagination_instance_ids_and_private_markers(self):
        service=Mock()
        page={'id':'series_20300321','summary':'Synthetic','start':{'dateTime':'2030-03-21T09:00:00+03:00'},'end':{'dateTime':'2030-03-21T10:00:00+03:00'},'attendees':[{'self':True,'responseStatus':'declined'}]}
        service.events.return_value.list.return_value.execute.side_effect=[{'items':[page],'nextPageToken':'next'},{'items':[]}]
        adapter=GoogleAdapter(service,'primary','UTC',True)
        result=adapter.read(NOW,NOW+timedelta(days=30))
        self.assertTrue(result.complete)
        self.assertEqual(result.events[0].id,'series_20300321')
        self.assertTrue(result.events[0].declined)
        self.assertEqual(service.events.return_value.list.call_args.kwargs['pageToken'],'next')
        event=Event('',NOW,NOW+timedelta(hours=1),marker='calendar-sync-v12:'+'a'*64)
        service.events.return_value.insert.return_value.execute.return_value={'id':'created'}
        adapter.create(event)
        kwargs=service.events.return_value.insert.call_args.kwargs
        self.assertEqual(kwargs['sendUpdates'],'none')
        self.assertEqual(kwargs['body']['visibility'],'default')
        self.assertEqual(kwargs['body']['attendees'],[])
        self.assertEqual(len(kwargs['body']['id']),64)
        self.assertFalse(kwargs['body']['reminders']['useDefault'])
    def test_shared_write_prohibited(self):
        adapter=GoogleAdapter(Mock(),'shared','UTC')
        with self.assertRaises(PermissionError):
            adapter.create(Event('',NOW,NOW+timedelta(hours=1)))
    def test_google_incomplete_response_and_all_day(self):
        service=Mock()
        service.events.return_value.list.return_value.execute.return_value={'items':[{'id':'bad'}]}
        adapter=GoogleAdapter(service,'primary','UTC')
        self.assertFalse(adapter.read(NOW,NOW+timedelta(days=30)).complete)
        e=adapter._event({'id':'all','start':{'date':'2030-03-21'},'end':{'date':'2030-03-22'}})
        self.assertTrue(e.all_day)
        self.assertEqual(adapter._body(e)['end'],{'date':'2030-03-22'})

class CliTests(unittest.TestCase):
    def test_dry_run_no_state_and_apply_repeat(self):
        import json
        with TemporaryDirectory() as directory:
            base=Path(directory)
            config={'mode':'fake','timezone':'UTC','state_file':'state.sqlite3','log_file':'sync.log','lock_file':'run.lock','fake':{n:n+'.json' for n in ('outlook','google','shared')}}
            (base/'config.json').write_text(json.dumps(config))
            from datetime import datetime
            start=datetime.now(timezone.utc)+timedelta(days=1)
            (base/'google.json').write_text(json.dumps([{'id':'synthetic','start':start.isoformat(),'end':(start+timedelta(hours=1)).isoformat()}]))
            args=['--config',str(base/'config.json')]
            before=(base/'google.json').read_bytes()
            self.assertEqual(main(args),0)
            self.assertFalse((base/'state.sqlite3').exists())
            self.assertEqual(before,(base/'google.json').read_bytes())
            self.assertEqual(main(['apply',*args]),0)
            remote=(base/'outlook.json').read_bytes()
            state=(base/'state.sqlite3').read_bytes()
            self.assertEqual(main(args),0)
            self.assertEqual(state,(base/'state.sqlite3').read_bytes())
            for _ in range(3):
                self.assertEqual(main(['apply',*args]),0)
            self.assertEqual(remote,(base/'outlook.json').read_bytes())
    def test_lock_excludes_another_run(self):
        with TemporaryDirectory() as directory:
            path=Path(directory)/'lock'
            with lock(path):
                with self.assertRaises(RuntimeError):
                    with lock(path):
                        pass


class ExtendedAdapterTests(unittest.TestCase):
    def test_google_tombstone_generation_and_collision_recovery(self):
        import hashlib
        error=Exception('Synthetic conflict')
        error.resp=Mock(status=409)
        service=Mock()
        adapter=GoogleAdapter(service,'primary','UTC',True)
        event=Event('',NOW,NOW+timedelta(hours=1),marker='calendar-sync-v12:'+'b'*64)
        calls=service.events.return_value
        calls.insert.return_value.execute.side_effect=[error,{'id':'new'}]
        calls.get.return_value.execute.return_value={'status':'cancelled'}
        self.assertEqual(adapter.create(event),'new')
        self.assertEqual(calls.insert.call_args.kwargs['body']['id'],hashlib.sha256((event.marker+':1').encode()).hexdigest())
        calls.insert.return_value.execute.side_effect=[error]
        calls.get.return_value.execute.return_value={'id':'recovered','extendedProperties':{'private':{'calendarSyncKey':event.marker}}}
        self.assertEqual(adapter.create(event),'recovered')
        calls.insert.return_value.execute.side_effect=[error]
        calls.get.return_value.execute.return_value={'id':'foreign'}
        with self.assertRaises(RuntimeError):
            adapter.create(event)
    def test_google_original_write_blocked(self):
        service=Mock()
        service.events.return_value.get.return_value.execute.return_value={'id':'original'}
        adapter=GoogleAdapter(service,'primary','UTC',True)
        event=Event('',NOW,NOW+timedelta(hours=1),marker='calendar-sync-v12:key')
        with self.assertRaises(RuntimeError):
            adapter.update('original',event)
        with self.assertRaises(RuntimeError):
            adapter.delete('original')
        service.events.return_value.update.assert_not_called()
        service.events.return_value.delete.assert_not_called()
    def test_outlook_mock_recurrence_move_original_identity(self):
        from calendar_sync.adapters import OutlookAdapter
        from types import SimpleNamespace as NS
        adapter=OutlookAdapter.__new__(OutlookAdapter)
        adapter.zone=timezone.utc
        start=NOW+timedelta(days=1,hours=3)
        item=Mock(Class=26,StartUTC=start,EndUTC=start+timedelta(hours=1),GlobalAppointmentID='series',IsRecurring=True,AllDayEvent=False,Subject='Synthetic',Body='Body',ResponseStatus=1,BusyStatus=0,Sensitivity=0,ReminderSet=True)
        item.UserProperties.Find.return_value=None
        item.Recipients.Count=0
        item.Attachments.Count=0
        item.Location=''
        exception=NS(Deleted=False,AppointmentItem=NS(StartUTC=start),OriginalDate=NOW+timedelta(days=1))
        exceptions=Mock(Count=1)
        exceptions.Item.return_value=exception
        item.GetRecurrencePattern.return_value=NS(Exceptions=exceptions)
        class Items:
            IncludeRecurrences=False
            def Sort(self, field):
                self.sorted=field
            def Restrict(self, expression):
                return [item]
        items=Items()
        adapter.folder=NS(Items=items)
        result=adapter.read(NOW,NOW+timedelta(days=30))
        self.assertEqual(result.events[0].id,'series:'+(NOW+timedelta(days=1)).isoformat())
        self.assertEqual(result.events[0].start,start)
        self.assertTrue(items.IncludeRecurrences)
    def test_outlook_start_is_local_wall_clock_even_when_com_tags_utc(self):
        from calendar_sync.adapters import OutlookAdapter
        from types import SimpleNamespace as NS
        from zoneinfo import ZoneInfo
        adapter=OutlookAdapter.__new__(OutlookAdapter)
        adapter.zone=ZoneInfo('Europe/Moscow')
        item=Mock(Start=datetime(2026,10,22,16,50,tzinfo=timezone.utc),End=datetime(2026,10,22,18,0,tzinfo=timezone.utc),GlobalAppointmentID='meeting',IsRecurring=False,AllDayEvent=False,Subject='Meeting',Body='',MeetingStatus=3,ResponseStatus=3,BusyStatus=2,Sensitivity=0,ReminderSet=False)
        item.UserProperties.Find.return_value=None
        item.Recipients.Count=0
        item.Attachments.Count=0
        item.Location=''
        event=adapter._event(item)
        self.assertEqual(event.start,datetime(2026,10,22,13,50,tzinfo=timezone.utc))
        self.assertEqual(event.end,datetime(2026,10,22,15,0,tzinfo=timezone.utc))
    def test_outlook_mock_write_no_send(self):
        from calendar_sync.adapters import OutlookAdapter
        adapter=OutlookAdapter.__new__(OutlookAdapter)
        adapter.zone=timezone.utc
        item=Mock(EntryID='synthetic-copy')
        item.UserProperties.Find.return_value=None
        item.Recipients.Count=0
        item.Attachments.Count=0
        item.Location=''
        event=Event('',NOW,NOW+timedelta(hours=1),'Больничка',marker='calendar-sync-v12:key',private=True)
        self.assertEqual(adapter._write(item,event),'synthetic-copy')
        item.Save.assert_called_once()
        item.Send.assert_not_called()
        self.assertEqual((item.BusyStatus,item.Sensitivity,item.ReminderSet,item.Body,item.Location),(2,2,False,'',''))
        self.assertEqual(item.StartUTC,NOW.astimezone(timezone.utc))
        self.assertEqual(item.EndUTC,(NOW+timedelta(hours=1)).astimezone(timezone.utc))
        all_day=replace(event,start=NOW.replace(hour=0),end=(NOW+timedelta(days=1)).replace(hour=0),all_day=True)
        adapter._write(item,all_day)
        self.assertEqual(item.StartUTC,all_day.start.astimezone(timezone.utc))
        self.assertEqual(item.EndUTC,all_day.end.astimezone(timezone.utc))
    def test_dst_and_overlapping_free_event(self):
        from zoneinfo import ZoneInfo
        zone=ZoneInfo('America/New_York')
        event=Event('dst',datetime(2030,3,10,3,5,tzinfo=zone),datetime(2030,3,10,3,15,tzinfo=zone),busy=False)
        source,target=FakeAdapter([event]),FakeAdapter()
        route=Route('dst','own',source,target,True)
        result=transform(route,event,identity(route,event))
        self.assertTrue(result.busy)
        self.assertEqual(result.start.astimezone(timezone.utc),event.start.astimezone(timezone.utc)-timedelta(minutes=15))
        self.assertEqual(result.end.astimezone(timezone.utc),event.end.astimezone(timezone.utc)+timedelta(minutes=15))

    def test_manual_privacy_and_content_changes_repaired(self):
        source=FakeAdapter([Event('original',NOW+timedelta(days=1),NOW+timedelta(days=1,hours=1),'Synthetic','Body')])
        target=FakeAdapter()
        route=Route('o2g','default',source,target,False)
        state=State()
        try:
            apply(route,state,plan(route,state,NOW))
            ident=next(iter(target.events))
            target.events[ident]=replace(target.events[ident],visibility='public',location='Changed',has_attendees=True,has_attachments=True)
            ops=plan(route,state,NOW)
            self.assertEqual([o.action for o in ops],['UPDATE'])
            self.assertEqual(apply(route,state,ops),0)
            self.assertEqual((target.events[ident].visibility,target.events[ident].location,target.events[ident].has_attendees,target.events[ident].has_attachments),('default','',False,False))
        finally:
            state.db.close()
    def test_transient_http_retry_bounded(self):
        source=FakeAdapter([Event('one',NOW+timedelta(days=1),NOW+timedelta(days=1,hours=1))])
        target=FakeAdapter()
        route=Route('retry','own',source,target,True)
        error=Exception('Sensitive API response')
        error.resp=Mock(status=503)
        target.create=Mock(side_effect=error)
        state=State()
        try:
            sleeper=Mock()
            self.assertEqual(apply(route,state,plan(route,state,NOW),sleeper=sleeper),1)
            self.assertEqual(target.create.call_count,3)
            self.assertEqual([c.args[0] for c in sleeper.call_args_list],[1,2])
        finally:
            state.db.close()
    def test_duplicate_source_identity_blocks(self):
        source=FakeAdapter()
        event=Event('one',NOW+timedelta(days=1),NOW+timedelta(days=1,hours=1))
        source.read=Mock(return_value=Snapshot([event,event]))
        state=State()
        try:
            with self.assertRaises(RuntimeError):
                plan(Route('duplicate','own',source,FakeAdapter(),True),state,NOW)
        finally:
            state.db.close()

    def test_state_commit_failure_recovers_without_duplicate(self):
        event=Event('one',NOW+timedelta(days=1),NOW+timedelta(days=1,hours=1))
        source,target=FakeAdapter([event]),FakeAdapter()
        route=Route('crash','own',source,target,True)
        state=State()
        try:
            with patch.object(state,'save',side_effect=OSError('Synthetic disk failure')):
                self.assertEqual(apply(route,state,plan(route,state,NOW)),1)
            self.assertEqual(len(target.events),1)
            self.assertEqual(apply(route,state,plan(route,state,NOW)),0)
            self.assertEqual(target.writes,1)
            self.assertEqual(len(state.rows('crash')),1)
        finally:
            state.db.close()
    def test_unavailable_direction_does_not_stop_healthy_route(self):
        import json
        with TemporaryDirectory() as directory:
            base=Path(directory)
            config={'mode':'fake','timezone':'UTC','state_file':'state.sqlite3','log_file':'sync.log','lock_file':'run.lock'}
            path=base/'config.json'
            path.write_text(json.dumps(config),encoding='utf-8')
            broken=FakeAdapter()
            broken.read=Mock(side_effect=ConnectionError('Sensitive provider message'))
            future=datetime.now(timezone.utc)+timedelta(days=1)
            source=FakeAdapter([Event('test',future,future+timedelta(hours=1))])
            target=FakeAdapter()
            routes=[Route('unavailable','one',broken,target,True),Route('healthy','two',source,target,True)]
            with patch('calendar_sync.__main__.build',return_value=routes):
                self.assertEqual(main(['apply','--config',str(path)]),1)
            self.assertEqual(len(target.events),1)
            self.assertNotIn('Sensitive',(base/'sync.log').read_text(encoding='utf-8'))

if __name__=='__main__':
    unittest.main()
