"""Behavior checks isolated from user data; no external requests or real emails."""
import datetime as dt
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
import threading
import urllib.request
import urllib.error
from unittest.mock import patch
from contextlib import closing

tmp = tempfile.TemporaryDirectory()
os.environ['AURADESK_DATA'] = tmp.name
import server as app


class DashboardTests(unittest.TestCase):
    def setUp(self):
        for p in Path(tmp.name).glob('*'):
            if p.is_file(): p.unlink()
        app.init()
        self.clock = dt.datetime(2026, 10, 6, 10, 0, tzinfo=app.TZ)
        self.patch = patch.object(app, 'now', return_value=self.clock)
        self.clock_mock = self.patch.start()

    def tearDown(self):
        self.patch.stop()

    def add(self, **extra):
        data = {'title':'Test OA','kind':'OA','due':'2026-10-08','due_time':'10:00','minutes':60, **extra}
        app.action('/api/item', data)
        return app.snapshot()['items'][-1]

    def test_persist_across_module_restart(self):
        item = self.add()
        spec = importlib.util.spec_from_file_location('restarted_app', Path(__file__).with_name('server.py'))
        restarted = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(restarted)
        restarted.init()
        with restarted.connect() as c:
            self.assertEqual(app.objects(c, 'items')[0]['id'], item['id'])

    def test_immediate_missed_penalty_idempotent_and_correction(self):
        item = self.add(due='2026-10-05')
        s=app.snapshot()
        self.assertEqual(s['points'], -30)
        self.assertEqual(s['items'][0]['status'], 'missed')
        self.assertEqual(app.snapshot()['points'], -30)
        app.action('/api/item', {**s['items'][0], 'status':'done','submitted_at':'2026-10-05T09:00:00-04:00'})
        self.assertEqual(app.snapshot()['points'], 30)

    def test_unknown_time_never_penalized(self):
        self.add(due='2026-10-01', due_time='')
        self.assertEqual(app.snapshot()['points'], 0)
        self.assertEqual(app.snapshot()['items'][0]['status'], 'active')

    def test_early_bonus_and_no_late_bonus(self):
        item=self.add()
        app.action('/api/item',{**item,'status':'done','submitted_at':'2026-10-06T10:00:00-04:00'})
        self.assertEqual(app.snapshot()['points'],40)
        app.action('/api/item',{**app.snapshot()['items'][0],'status':'done','submitted_at':'2026-10-09T10:00:00-04:00'})
        self.assertEqual(app.snapshot()['points'],0)

    def test_practice_once_per_day_and_undo(self):
        for _ in range(2): app.action('/api/practice',{'kind':'LeetCode','minutes':15})
        s=app.snapshot()
        self.assertEqual(s['points'],10)
        self.assertEqual(s['streak'],1)
        app.action('/api/practice-delete',{'id':s['practice'][0]['id']})
        self.assertEqual(app.snapshot()['points'],10)
        app.action('/api/practice-delete',{'id':s['practice'][1]['id']})
        self.assertEqual(app.snapshot()['points'],0)

    def test_unlocked_rewards_survive_deduction(self):
        app.action('/api/reward',{'title':'Treat','threshold':10})
        app.action('/api/practice',{'kind':'LeetCode','minutes':15})
        self.add(due='2026-10-05')
        s=app.snapshot()
        self.assertEqual(s['points'],-20)
        self.assertEqual(s['rewards'][0]['unlocked'],1)

    def test_archive_retains_penalty_and_restores(self):
        item=self.add(due='2026-10-05')
        app.action('/api/item',{**item,'status':'archived'})
        self.assertEqual(app.snapshot()['points'],-30)
        archived=app.snapshot()['items'][0]
        self.assertEqual(archived['previous_status'],'missed')
        app.action('/api/item',{**archived,'status':archived['previous_status']})
        self.assertEqual(app.snapshot()['points'],-30)

    def test_top_three_maximum(self):
        for n in range(3): self.add(title=str(n),priority_day='2026-10-06')
        with self.assertRaises(ValueError): self.add(priority_day='2026-10-06')

    def test_single_urgent_email_no_coursework_and_cancel(self):
        s=app.settings()
        s.update(email_enabled=True,school_email='student@example.edu',smtp_host='smtp.example.com',smtp_user='sender@example.com',smtp_password='fake')
        app.save_settings(s)
        item=self.add()
        self.add(title='HW',kind='Homework')
        app.deadlines();app.deadlines()
        with app.connect() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM notices').fetchone()[0],1)
        app.action('/api/item',{**item,'status':'done'})
        with app.connect() as c:
            self.assertEqual(c.execute('SELECT status FROM notices').fetchone()[0],'cancelled')

    def test_urgent_then_missed_emails_only_once(self):
        s=app.settings();s['email_enabled']=True;app.save_settings(s)
        self.add()
        self.clock=dt.datetime(2026,10,9,10,tzinfo=app.TZ)
        self.patch.return_value=self.clock
        with patch.object(app,'now',return_value=self.clock):
            app.deadlines();app.deadlines()
        with app.connect() as c:
            rows=c.execute('SELECT id,status FROM notices').fetchall()
            self.assertEqual(len(rows),2)
            self.assertEqual(rows[0]['status'],'cancelled')

    def test_source_parser_and_hidden_choice_survives_refresh(self):
        fixture='<table><tr><td>Company A</td><td>Software Engineer New Grad 2027</td><td>NYC</td><td><a href="https://example.com/job?utm_source=x">Apply</a></td></tr><tr><td>↳</td><td>Backend Engineer</td><td>Remote</td><td><a href="https://example.com/second">Apply</a></td></tr></table>'
        parsed=app.parse_jobs(fixture,'fixture')
        self.assertEqual(len(parsed),2)
        self.assertEqual(parsed[1]['company'],'Company A')
        self.assertEqual(parsed[0]['url'],'https://example.com/job')
        with patch.object(app,'fetch',return_value=fixture):
            app.refresh_jobs()
            id=app.snapshot()['jobs'][0]['id']
            app.action('/api/job',{'id':id,'status':'archived'})
            app.refresh_jobs()
        self.assertEqual(next(j for j in app.snapshot()['jobs'] if j['id']==id)['status'],'archived')

    def test_source_failure_preserves_saved_roles(self):
        app.action('/api/manual-job',{'company':'A','title':'Engineer','url':'https://example.com/job'})
        with patch.object(app,'fetch',side_effect=OSError('offline')): app.refresh_jobs()
        self.assertEqual(len(app.snapshot()['jobs']),1)

    def test_offline_capture_missing_time_and_relative_dates(self):
        settings=app.settings();settings['capture_provider']='local';app.save_settings(settings)
        raw,_=app.draft('yo add Wells Fargo OA due 9/30 60min https://example.com/oa')
        self.assertEqual(raw['due'],'2026-09-30')
        self.assertEqual(raw['due_time'],'')
        self.assertEqual(raw['minutes'],60)
        raw,_=app.draft('add hw due tomorrow at 5pm')
        self.assertEqual(raw['due'],'2026-10-07')
        self.assertEqual(raw['due_time'],'17:00')

    def test_secrets_not_returned_and_backup_is_readable(self):
        s=app.settings();s.update(gemini_key='secret1',smtp_password='secret2');app.save_settings(s)
        self.assertNotIn('secret',json.dumps(app.snapshot()))
        item=self.add();app.backup(force=True)
        p=Path(tmp.name)/'backups/dashboard-2026-10-06.sqlite'
        with closing(sqlite3.connect(p)) as c: self.assertEqual(c.execute('SELECT COUNT(*) FROM items').fetchone()[0],1)

    def test_http_write_validation_and_host_protection(self):
        from http.server import ThreadingHTTPServer
        try:
            httpd=ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        except PermissionError:
            self.skipTest('Environment disallows binding a local test socket')
        previous=app.PORT;app.PORT=httpd.server_port
        threading.Thread(target=httpd.serve_forever,daemon=True).start()
        origin='http://127.0.0.1:'+str(app.PORT)
        try:
            with urllib.request.urlopen(origin+'/api/state') as r:
                self.assertEqual(json.load(r)['points'],0)
            req=urllib.request.Request(origin+'/api/item',data=b'{}',headers={'Content-Type':'application/json'})
            with self.assertRaises(urllib.error.HTTPError) as result: urllib.request.urlopen(req)
            self.assertEqual(result.exception.code,403)
            payload={'title':'HTTP OA','kind':'OA','due':'2026-10-10','due_time':'17:00'}
            req=urllib.request.Request(origin+'/api/item',data=json.dumps(payload).encode(),headers={'Content-Type':'application/json','X-Aura-Token':app.TOKEN,'Origin':origin})
            with urllib.request.urlopen(req) as r: self.assertTrue(json.load(r)['ok'])
            req=urllib.request.Request(origin+'/api/state',headers={'Host':'evil.example'})
            with self.assertRaises(urllib.error.HTTPError) as result: urllib.request.urlopen(req)
            self.assertEqual(result.exception.code,403)
        finally:
            httpd.shutdown();httpd.server_close();app.PORT=previous

    def test_email_delivery_record_and_retry(self):
        s=app.settings();s.update(email_enabled=True,school_email='student@example.edu',smtp_host='smtp.example.com',smtp_user='sender@example.com',smtp_password='fake');app.save_settings(s)
        self.add()
        with patch.object(app.smtplib,'SMTP_SSL',side_effect=OSError('offline')):
            app.mail_once()
        with app.connect() as c: self.assertEqual(c.execute('SELECT status FROM notices').fetchone()[0],'pending')
        with patch.object(app.smtplib,'SMTP_SSL') as smtp:
            app.mail_once();app.mail_once()
            self.assertEqual(smtp.return_value.send_message.call_count,1)
        with app.connect() as c: self.assertEqual(c.execute('SELECT status FROM notices').fetchone()[0],'sent')

    def test_action_completion_and_retry_do_not_complete_parent(self):
        item=self.add(kind='Project',next_action='Read spec',priority_day='2026-10-06')
        aid=item['current_action']['id']
        for _ in range(2): app.action('/api/action-complete',{'id':item['id'],'action_id':aid})
        saved=app.snapshot()['items'][0]
        self.assertEqual(saved['status'],'active')
        self.assertEqual(saved['priority_day'],'')
        self.assertIsNone(saved['current_action'])
        self.assertEqual(len(saved['action_history']),1)
        app.action('/api/action-undo',{'id':item['id'],'action_id':aid})
        self.assertEqual(app.snapshot()['items'][0]['current_action']['text'],'Read spec')
        with self.assertRaisesRegex(ValueError,'changed'):
            app.action('/api/item',{**item,'notes':'stale editor'})

    def test_plan_advances_preserves_progress_and_explicit_parent(self):
        item=self.add(kind='Project',plan_steps=[{'id':'a','text':'Inspect'},{'id':'b','text':'Write'},{'id':'c','text':'Submit'}])
        app.action('/api/action-complete',{'id':item['id'],'action_id':'a'})
        saved=app.snapshot()['items'][0]
        self.assertEqual(saved['current_action']['id'],'b')
        steps=saved['plan_steps'];steps.reverse();steps[2]['text']='Inspect the spec'
        app.action('/api/item',{'id':item['id'],'revision':saved['revision'],'plan_steps':steps})
        saved=app.snapshot()['items'][0]
        self.assertTrue(saved['plan_steps'][2]['completed_at'])
        self.assertEqual(saved['current_action']['id'],'c')
        for aid in ['c','b']: app.action('/api/action-complete',{'id':item['id'],'action_id':aid})
        saved=app.snapshot()['items'][0]
        self.assertEqual(saved['status'],'active');self.assertIsNone(saved['current_action'])
        app.action('/api/item',{'id':item['id'],'revision':saved['revision'],'status':'done'})
        self.assertEqual(app.snapshot()['items'][0]['status'],'done')

    def test_removed_steps_are_recoverable_and_omitted_fields_survive(self):
        item=self.add(kind='Project',plan_steps=[{'id':'a','text':'Read'},{'id':'b','text':'Write'}])
        app.action('/api/action-complete',{'id':item['id'],'action_id':'a'})
        saved=app.snapshot()['items'][0]
        app.action('/api/item',{'id':item['id'],'revision':saved['revision'],'plan_steps':saved['plan_steps'][1:]})
        saved=app.snapshot()['items'][0]
        self.assertTrue(saved['removed_plan_steps'][0]['completed_at'])
        app.action('/api/item',{'id':item['id'],'notes':'Changed'})
        saved=app.snapshot()['items'][0]
        self.assertEqual(len(saved['action_history']),1)
        self.assertEqual(saved['plan_steps'][0]['id'],'b')
        app.action('/api/item',{'id':item['id'],'plan_steps':saved['removed_plan_steps']+saved['plan_steps']})
        self.assertEqual(app.snapshot()['items'][0]['current_action']['id'],'b')

    def test_oa_action_does_not_award_submission_or_cancel_notice(self):
        settings=app.settings();settings['email_enabled']=True;app.save_settings(settings)
        item=self.add(next_action='Read instructions')
        app.action('/api/action-complete',{'id':item['id'],'action_id':item['current_action']['id']})
        self.assertEqual(app.snapshot()['points'],0)
        with app.connect() as c: self.assertEqual(c.execute('SELECT status FROM notices').fetchone()[0],'pending')
        saved=app.snapshot()['items'][0]
        app.action('/api/item',{'id':saved['id'],'status':'done','submitted_date':'2026-10-06','submitted_time':'10:00'})
        self.assertEqual(app.snapshot()['points'],40)

    def test_acknowledgment_retains_penalty_and_actual_time_correction(self):
        item=self.add(due='2026-10-05')
        app.action('/api/miss-acknowledge',{'id':item['id']})
        app.init();saved=app.snapshot()['items'][0]
        self.assertTrue(saved['miss_acknowledged']);self.assertEqual(app.snapshot()['points'],-30)
        for _ in range(2):
            app.action('/api/item',{'id':item['id'],'status':'done','submitted_date':'2026-10-05','submitted_time':'09:00'})
        self.assertEqual(app.snapshot()['points'],30)

    def test_deadline_corrections_cancel_stale_notice_and_remain_idempotent(self):
        settings=app.settings();settings['email_enabled']=True;app.save_settings(settings)
        item=self.add()
        app.action('/api/item',{'id':item['id'],'due':'2026-10-05'})
        missed=app.snapshot()['items'][0];self.assertEqual(app.snapshot()['points'],-30)
        app.action('/api/miss-acknowledge',{'id':item['id']})
        app.action('/api/item',{'id':item['id'],'due':'2026-10-04'})
        saved=app.snapshot()['items'][0]
        self.assertFalse(saved['miss_acknowledged']);self.assertEqual(app.snapshot()['points'],-30)
        app.action('/api/item',{'id':item['id'],'due':'2026-10-20'})
        for _ in range(2): app.snapshot()
        self.assertEqual(app.snapshot()['points'],0)
        with app.connect() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM notices WHERE status='pending'").fetchone()[0],0)

    def test_suggestions_balance_early_coursework_and_oa(self):
        for n in range(10): self.add(title='OA '+str(n),due='2026-10-30')
        project=self.add(kind='Project',start_by='2026-10-06',due='2026-10-15',minutes=300)
        ids=app.snapshot()['suggestions']
        self.assertEqual(ids[0],project['id'])
        self.assertTrue(any(i['kind']=='OA' and i['id'] in ids for i in app.snapshot()['items']))
        self.assertEqual(app.snapshot()['items'][-1]['guidance']['reason'],'Start-by date reached')
        for n in range(6): self.add(kind='Project',title='Coursework '+str(n),start_by='2026-10-01',due='2026-10-12')
        ids=app.snapshot()['suggestions']
        self.assertEqual(len(ids),5)
        self.assertTrue(any(i['kind']=='OA' and i['id'] in ids for i in app.snapshot()['items']))

    def test_unknown_scope_kickoff_and_urgent_selection_not_suggested(self):
        item=self.add(kind='Project',due='',due_time='',minutes=0,priority_day='2026-10-06')
        self.assertEqual(item['current_action']['text'],'Inspect the spec and estimate the work.')
        self.assertEqual(item['minutes'],0);self.assertEqual(item['start_by'],'')
        self.assertNotIn(item['id'],app.snapshot()['suggestions'])
        oa=self.add(priority_day='2026-10-06')
        self.assertTrue(oa['guidance']['attention']);self.assertNotIn(oa['id'],app.snapshot()['suggestions'])

    def test_course_links_restart_backup_and_validation(self):
        app.action('/api/course-link',{'label':'EECS','url':'https://canvas.example.edu/course'})
        app.init();link=app.snapshot()['course_links'][0]
        app.action('/api/course-link',{'id':link['id'],'label':'New label','url':link['url']})
        self.assertEqual(app.snapshot()['course_links'][0]['label'],'New label')
        app.backup(force=True)
        with closing(sqlite3.connect(Path(tmp.name)/'backups/dashboard-2026-10-06.sqlite')) as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM course_links').fetchone()[0],1)
        for url in ['javascript:alert(1)','https://','https://user:pass@example.com']:
            with self.assertRaises(ValueError): app.action('/api/course-link',{'label':'Bad','url':url})
        app.action('/api/course-link-remove',{'id':link['id']});self.assertEqual(app.snapshot()['course_links'],[])

    def activate_habits(self):
        with app.connect() as c: c.execute("UPDATE meta SET value='2026-10-01' WHERE key IN ('habit_activation','design_streak_activation')")

    def test_independent_habit_progression_cap_reset_and_duplicate_logs(self):
        self.activate_habits()
        self.clock_mock.return_value=dt.datetime(2026,10,10,10,tzinfo=app.TZ)
        for n in range(7):
            day=(dt.date(2026,10,1)+dt.timedelta(days=n)).isoformat()
            for _ in range(2): app.action('/api/practice',{'kind':'LeetCode','minutes':1,'day':day})
        values=[r['points'] for r in sorted(app.snapshot()['ledger'],key=lambda r:r['day'])]
        self.assertEqual(values,[10,12,14,16,18,20,20])
        self.assertEqual(app.snapshot()['habits']['LeetCode']['points'],10)
        app.action('/api/application',{'day':'2026-10-09'})
        self.assertEqual(app.snapshot()['habits']['Applications']['points'],12)
        self.assertEqual(app.snapshot()['habits']['LeetCode']['points'],10)
        for _ in range(2): app.action('/api/application',{'day':'2026-10-10'})
        self.assertEqual(sum(r['points'] for r in app.snapshot()['ledger'] if r['id'].startswith('application:')),22)
        app.action('/api/practice',{'kind':'System design','minutes':10})
        self.assertEqual(next(r['points'] for r in app.snapshot()['ledger'] if r['id'].startswith('practice:System')),15)

    def test_habit_undo_backdate_recalculates_but_preserves_legacy_ledger(self):
        self.activate_habits()
        for day in ['2026-10-01','2026-10-03']: app.action('/api/application',{'day':day})
        app.action('/api/application',{'day':'2026-10-02'})
        self.assertEqual(sum(r['points'] for r in app.snapshot()['ledger']),36)
        middle=next(a for a in app.snapshot()['applications'] if a['day']=='2026-10-02')
        app.action('/api/application-delete',{'id':middle['id']})
        self.assertEqual(app.snapshot()['points'],20)
        with app.connect() as c:
            c.execute("INSERT INTO practice VALUES('legacy','LeetCode','2026-09-30',10,'')")
            c.execute("INSERT INTO ledger VALUES('practice:LeetCode:2026-09-30','Legacy',10,'2026-09-30','old')")
        app.action('/api/practice',{'kind':'LeetCode','minutes':1,'day':'2026-10-01'})
        self.assertEqual(next(r['points'] for r in app.snapshot()['ledger'] if r['day']=='2026-10-01' and r['id'].startswith('practice')),12)
        self.assertEqual(next(r['points'] for r in app.snapshot()['ledger'] if r['id'].endswith('2026-09-30')),10)

    def test_applied_logs_once_and_archive_refresh_does_not_erase(self):
        self.activate_habits()
        app.action('/api/manual-job',{'company':'A','title':'Engineer','url':'https://example.com/job'})
        job=app.snapshot()['jobs'][0]
        for _ in range(2): app.action('/api/job',{'id':job['id'],'status':'applied'})
        self.assertEqual(len(app.snapshot()['applications']),1);self.assertEqual(app.snapshot()['points'],10)
        app.action('/api/job',{'id':job['id'],'status':'archived'})
        self.assertEqual(len(app.snapshot()['applications']),1)
        log=app.snapshot()['applications'][0]
        app.action('/api/application',{**log,'day':'2026-10-05'})
        app.init();self.assertEqual(app.snapshot()['applications'][0]['day'],'2026-10-05')

    def test_legacy_record_upgrade_preserves_ids_and_custom_data(self):
        legacy={'id':'old','title':'Old homework','kind':'Homework','status':'active','minutes':30,'due':'','due_time':'','next_action':'Read','notes':'Keep me','resources':[],'custom_field':'keep'}
        with app.connect() as c: app.put(c,'items',legacy)
        app.init();saved=app.snapshot()['items'][0]
        self.assertEqual(saved['current_action']['text'],'Read')
        app.action('/api/item',{'id':'old','title':'Edited'})
        saved=app.snapshot()['items'][0]
        self.assertEqual(saved['notes'],'Keep me');self.assertEqual(saved['custom_field'],'keep')

    def test_detroit_deadline_dst_and_unknown_time_guidance(self):
        item=self.add(due='2026-11-02',due_time='10:00')
        self.assertEqual(app.parse_due(item).utcoffset(),dt.timedelta(hours=-5))
        date_only=self.add(due='2026-10-06',due_time='')
        self.assertTrue(date_only['guidance']['attention'])
        self.assertIn('time unspecified',date_only['guidance']['reason'])
        self.assertEqual(date_only['status'],'active')

    def test_capture_failure_preserves_offline_fallback(self):
        with patch.object(app,'draft',side_effect=OSError('offline')):
            result=app.action('/api/draft',{'text':'add homework due tomorrow'})
        self.assertEqual(result['draft']['kind'],'Homework')
        self.assertIn('Offline',result['message'])

    def test_upgrade_backup_is_taken_before_schema_changes(self):
        item=self.add(kind='Homework')
        before=app.settings();before['gemini_key']='keep-key';app.save_settings(before)
        with app.connect() as c: c.execute('DROP TABLE applications');c.execute('DROP TABLE course_links')
        app.init()
        backups=list((Path(tmp.name)/'backups').glob('pre-productivity-*.sqlite'))
        self.assertTrue(backups)
        with closing(sqlite3.connect(backups[-1])) as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM items').fetchone()[0],1)
            self.assertIsNone(c.execute("SELECT name FROM sqlite_master WHERE name='applications'").fetchone())
        self.assertEqual(app.snapshot()['items'][0]['id'],item['id'])
        self.assertEqual(app.settings()['gemini_key'],'keep-key')

    def test_plan_supersedes_old_action_and_retry_cannot_finish_next_step(self):
        item=self.add(kind='Project',next_action='Old free text',plan_steps=[{'id':'a','text':'Inspect'},{'id':'b','text':'Write'}])
        app.action('/api/action-complete',{'id':item['id'],'action_id':'a'})
        app.action('/api/action-complete',{'id':item['id'],'action_id':'a'})
        self.assertEqual(app.snapshot()['items'][0]['current_action']['id'],'b')
        app.action('/api/action-complete',{'id':item['id'],'action_id':'b'})
        self.assertIsNone(app.snapshot()['items'][0]['current_action'])
        self.assertEqual(app.snapshot()['items'][0]['status'],'active')

    def test_action_undo_respects_full_priority_list(self):
        item=self.add(kind='Project',next_action='Inspect',priority_day='2026-10-06')
        app.action('/api/action-complete',{'id':item['id'],'action_id':item['current_action']['id']})
        for n in range(3): self.add(title=str(n),priority_day='2026-10-06')
        app.action('/api/action-undo',{'id':item['id'],'action_id':item['current_action']['id']})
        saved=next(i for i in app.snapshot()['items'] if i['id']==item['id'])
        self.assertEqual(saved['priority_day'],'');self.assertEqual(saved['current_action']['text'],'Inspect')

    def test_application_streak_dates_across_daylight_saving(self):
        self.activate_habits();self.clock_mock.return_value=dt.datetime(2026,11,3,10,tzinfo=app.TZ)
        for day in ['2026-10-31','2026-11-01','2026-11-02']:app.action('/api/application',{'day':day})
        h=app.snapshot()['habits']['Applications']
        self.assertEqual(h['streak'],3);self.assertEqual(h['points'],16);self.assertFalse(h['done'])

    def test_capture_ordered_actions_review_save_and_advance(self):
        settings=app.settings();settings['capture_provider']='local';app.save_settings(settings)
        response=app.action('/api/draft',{'text':'Add report project. Next actions:\n- Read spec\n- Outline\n- Write draft'})
        draft=response['draft']
        self.assertEqual([x['text'] for x in draft['plan_steps']],['Read spec','Outline','Write draft'])
        self.assertEqual(app.snapshot()['items'],[])
        app.action('/api/item',draft)
        item=app.snapshot()['items'][0]
        app.action('/api/action-complete',{'id':item['id'],'action_id':item['current_action']['id']})
        updated=app.snapshot()['items'][0]
        self.assertEqual(updated['status'],'active');self.assertEqual(updated['current_action']['text'],'Outline')

    def test_capture_ai_actions_and_inline_offline_actions(self):
        with patch.object(app,'draft',return_value=({'title':'Homework','kind':'Homework','next_actions':['Read','Solve','Check']},'AI draft')):
            result=app.action('/api/draft',{'text':'Homework with three actions'})['draft']
        self.assertEqual([x['text'] for x in result['plan_steps']],['Read','Solve','Check'])
        self.assertEqual(len({x['id'] for x in result['plan_steps']}),3)
        parsed,_=app.offline_draft('Add homework. Next actions: Read, Solve, Check')
        self.assertEqual(parsed['next_actions'],['Read','Solve','Check'])

    def test_capture_status_does_not_claim_verified_inference(self):
        result=type('Result',(),{'returncode':0,'stdout':'Logged in using ChatGPT','stderr':''})()
        with patch.object(app.shutil,'which',return_value='/fake/codex'),patch.object(app.subprocess,'run',return_value=result) as invoke:
            status=app.action('/api/capture-status',{})['status']
        self.assertIn('unverified',status);self.assertNotIn('deferred',status)
        self.assertEqual(invoke.call_args.args[0],['/fake/codex','login','status'])

    def test_restored_deadline_requeues_cancelled_but_not_sent_email(self):
        settings=app.settings();settings['email_enabled']=True;app.save_settings(settings)
        item=self.add()
        app.action('/api/item',{'id':item['id'],'due':'2026-10-20'})
        app.action('/api/item',{'id':item['id'],'due':'2026-10-08'})
        app.snapshot()
        with app.connect() as c:
            self.assertEqual(c.execute('SELECT status FROM notices').fetchone()[0],'pending')
            c.execute("UPDATE notices SET status='sent'")
        app.action('/api/item',{'id':item['id'],'due':'2026-10-20'})
        app.action('/api/item',{'id':item['id'],'due':'2026-10-08'})
        app.snapshot()
        with app.connect() as c:self.assertEqual(c.execute('SELECT status FROM notices').fetchone()[0],'sent')

    def test_activity_creation_times_persist_and_corrections_keep_order(self):
        app.action('/api/practice',{'kind':'System design','minutes':10})
        self.clock_mock.return_value=self.clock+dt.timedelta(minutes=1)
        app.action('/api/application',{'day':'2026-10-05','company':'Example'})
        saved=app.snapshot();practice=saved['practice'][0];application=saved['applications'][0]
        self.assertGreater(application['created'],practice['created'])
        self.clock_mock.return_value=self.clock+dt.timedelta(minutes=2)
        app.action('/api/application',{**application,'day':'2026-10-06'})
        app.init()
        self.assertEqual(app.snapshot()['applications'][0]['created'],application['created'])
        self.assertEqual(app.snapshot()['practice'][0]['created'],practice['created'])

    def test_system_design_three_day_streak_cap_reset_duplicate_and_undo(self):
        self.activate_habits();self.clock_mock.return_value=dt.datetime(2026,11,1,10,tzinfo=app.TZ)
        for day in ['2026-10-01','2026-10-04','2026-10-07','2026-10-10','2026-10-13','2026-10-16','2026-10-19']:
            for _ in range(2):app.action('/api/practice',{'kind':'System design','minutes':10,'day':day})
        awards=sorted((r for r in app.snapshot()['ledger'] if r['id'].startswith('practice:System')),key=lambda r:r['day'])
        self.assertEqual([r['points'] for r in awards],[15,17,19,21,23,25,25])
        self.clock_mock.return_value=dt.datetime(2026,10,22,10,tzinfo=app.TZ)
        h=app.snapshot()['habits']['System design'];self.assertEqual(h['streak'],7);self.assertEqual(h['points'],25)
        self.clock_mock.return_value=dt.datetime(2026,10,23,10,tzinfo=app.TZ)
        h=app.snapshot()['habits']['System design'];self.assertEqual(h['streak'],0);self.assertEqual(h['points'],15)
        app.action('/api/practice',{'kind':'System design','minutes':10})
        logs=[p for p in app.snapshot()['practice'] if p['day']=='2026-10-10']
        for log in logs:app.action('/api/practice-delete',{'id':log['id']})
        later=next(r for r in app.snapshot()['ledger'] if r['id'].endswith('2026-10-13'))
        self.assertEqual(later['points'],15)

    def test_system_design_upgrade_does_not_reprice_existing_awards(self):
        self.activate_habits()
        with app.connect() as c:
            c.execute("DELETE FROM meta WHERE key='design_streak_activation'")
            c.execute("INSERT INTO practice VALUES('old-design','System design','2026-10-06',10,'')")
            c.execute("INSERT INTO ledger VALUES('practice:System design:2026-10-06','Old design',10,'2026-10-06','old')")
        app.init()
        app.action('/api/practice',{'kind':'System design','minutes':10})
        self.assertEqual(app.snapshot()['points'],10)
        for log in app.snapshot()['practice']:app.action('/api/practice-delete',{'id':log['id']})
        app.action('/api/practice',{'kind':'System design','minutes':10})
        self.assertEqual(app.snapshot()['points'],10)
        self.clock_mock.return_value=self.clock+dt.timedelta(days=3)
        app.action('/api/practice',{'kind':'System design','minutes':10})
        self.assertEqual(app.snapshot()['points'],27)
        app.init();app.action('/api/application',{})
        self.assertEqual(next(r['points'] for r in app.snapshot()['ledger'] if r['id']=='practice:System design:2026-10-09'),17)

    def test_practice_edit_preserves_identity_and_reconciles_rewards(self):
        self.activate_habits()
        for day in ['2026-10-01','2026-10-02']:app.action('/api/practice',{'kind':'LeetCode','minutes':10,'day':day})
        log=next(p for p in app.snapshot()['practice'] if p['day']=='2026-10-02')
        app.action('/api/practice-edit',{'id':log['id'],'day':'2026-10-04','minutes':20,'notes':'Edited'})
        saved=next(p for p in app.snapshot()['practice'] if p['id']==log['id'])
        self.assertEqual(saved['created'],log['created']);self.assertEqual(saved['notes'],'Edited')
        self.assertEqual(app.snapshot()['points'],20)
        with self.assertRaises(ValueError):app.action('/api/practice-edit',{'id':log['id'],'day':'2027-01-01'})

    def test_parent_completion_history_survives_reopen_and_correction(self):
        item=self.add(kind='Homework')
        app.action('/api/item',{'id':item['id'],'status':'done'})
        saved=app.snapshot()['items'][0];first=saved['completion_events'][0]['id']
        app.action('/api/item',{'id':item['id'],'status':'done','submitted_at':'2026-10-05T10:00:00-04:00'})
        saved=app.snapshot()['items'][0]
        self.assertEqual(len(saved['completion_events']),1);self.assertEqual(saved['completion_events'][0]['id'],first)
        app.action('/api/item',{'id':item['id'],'status':'active','submitted_at':''})
        self.assertEqual(len(app.snapshot()['items'][0]['completion_events']),1)
        app.action('/api/item',{'id':item['id'],'status':'done'})
        self.assertEqual(len(app.snapshot()['items'][0]['completion_events']),2)

if __name__=='__main__': unittest.main(verbosity=2)
