"""Task timer: server-side state machine and the worked examples X1-X10 (docs/release-v6/04-specifications.md 3.5)."""
import datetime
from unittest import mock
from zoneinfo import ZoneInfo

from core.models import NotificationJob, Record, WorkSession
from core.v6 import worktime as W
from core.v6.reports import split_days

from .helpers import Base, task

IST = ZoneInfo('Asia/Kolkata')


class Clock:
    def __init__(self, dt):
        self.t = dt

    def __call__(self):
        return self.t

    def set(self, h, m=0, day=0):
        self.t = datetime.datetime.combine(self.base_date + datetime.timedelta(days=day), datetime.time(h, m), tzinfo=IST).astimezone(datetime.timezone.utc)


def clock_on(d, h, m=0):
    c = Clock(None)
    c.base_date = d
    c.set(h, m)
    return c


class Timer(Base):
    def setUp(self):
        self.day = datetime.date.today() - datetime.timedelta(days=1)
        self.clk = clock_on(self.day, 10, 0)
        p = mock.patch('core.v6.worktime.now', self.clk)
        p.start()
        self.addCleanup(p.stop)
        r = Record.objects.get(coll='tasks', rid='t_rahul')
        r.data = dict(r.data, est=300, due=(datetime.date.today() + datetime.timedelta(days=5)).isoformat())
        r.save()

    def act(self, action, **b):
        return self.api(self.em, action, b, method='POST')

    def test_x1_three_sessions_and_metrics(self):
        for (h0, m0), (h1, m1) in (((10, 0), (11, 30)), ((12, 0), (14, 0)), ((15, 0), (16, 0))):
            self.clk.set(h0, m0)
            s = self.act('time.start', taskId='t_rahul')['session']
            self.clk.set(h1, m1)
            self.act('time.stop', sessionId=s['id'])
        tot = W.totals(['t_rahul'])['t_rahul']
        self.assertEqual(tot['actual'], 270 * 60)
        m = W.metrics(300, tot['actual'])
        self.assertEqual((m['remaining'], m['variance'], m['variancePct'], m['efficiency']), (1800, -1800, -10.0, 111.1))
        self.assertEqual(Record.objects.get(coll='tasks', rid='t_rahul').data['status'], 'doing')

    def test_x2_pause_not_counted(self):
        s = self.act('time.start', taskId='t_rahul')['session']
        self.clk.set(10, 45)
        self.act('time.pause', sessionId=s['id'], reason='client_call')
        self.clk.set(11, 0)
        self.act('time.resume', sessionId=s['id'])
        self.clk.set(12, 0)
        s = self.act('time.stop', sessionId=s['id'])['session']
        self.assertEqual((s['work'], s['pause']), (105 * 60, 15 * 60))

    def test_x3_overrun_alerts_once_each(self):
        r = Record.objects.get(coll='tasks', rid='t_rahul')
        r.data = dict(r.data, est=120)
        r.save()
        s = self.act('time.start', taskId='t_rahul')['session']
        self.clk.set(12, 30)
        self.act('time.stop', sessionId=s['id'])
        m = W.metrics(120, W.totals(['t_rahul'])['t_rahul']['actual'])
        self.assertEqual((m['over'], m['efficiency'], m['variancePct']), (1800, 80.0, 25.0))
        keys = sorted(NotificationJob.objects.filter(event_key='task.time_exceeded').values_list('occurrence_key', flat=True))
        self.assertEqual(keys, ['p100', 'p80'])
        W.check_overrun('t_rahul')
        self.assertEqual(NotificationJob.objects.filter(event_key='task.time_exceeded').count(), 2)

    def test_x4_x5_manual_pending_then_decided(self):
        self.clk.set(10, 0)
        s = self.act('time.start', taskId='t_rahul')['session']
        self.clk.set(12, 0)
        self.act('time.stop', sessionId=s['id'])
        self.clk.set(18, 0)
        r = self.act('time.manual.create', taskId='t_rahul', date=self.day.isoformat(), start='14:00', end='14:45', reason='Worked at the client office', category='client_site')
        self.assertEqual(r['session']['approval'], 'pending')
        tot = W.totals(['t_rahul'])['t_rahul']
        self.assertEqual((tot['actual'], tot['pending']), (7200, 2700))
        own = self.api(self.em, 'time.approvals.decide', {'ids': ['m:' + r['session']['id']], 'decision': 'approve'}, method='POST')
        self.assertEqual(own['_status'], 403)  # employees cannot approve
        bm = self.api(self.bm, 'time.approvals.decide', {'ids': ['m:' + r['session']['id']], 'decision': 'approve'}, method='POST')
        self.assertEqual(bm['done'], ['m:' + r['session']['id']])
        self.assertEqual(W.totals(['t_rahul'])['t_rahul']['actual'], 7200 + 2700)

    def test_manual_validations(self):
        self.clk.set(18, 0)
        b = dict(taskId='t_rahul', date=self.day.isoformat(), start='14:00', end='14:45', reason='short')
        self.assertEqual(self.act('time.manual.create', **b)['code'], 'VALIDATION')
        b['reason'] = 'Worked offline at home'
        b['end'] = '13:00'
        self.assertEqual(self.act('time.manual.create', **b)['code'], 'VALIDATION')
        b['end'] = '14:45'
        self.assertTrue(self.act('time.manual.create', **b)['ok'])
        self.assertEqual(self.act('time.manual.create', **dict(b, start='14:30', end='15:00'))['code'], 'OVERLAP')
        old = (self.day - datetime.timedelta(days=10)).isoformat()
        self.assertEqual(self.act('time.manual.create', **dict(b, date=old))['code'], 'VALIDATION')
        future = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
        self.assertEqual(self.act('time.manual.create', **dict(b, date=future))['code'], 'VALIDATION')

    def test_reject_needs_comment(self):
        self.clk.set(18, 0)
        r = self.act('time.manual.create', taskId='t_rahul', date=self.day.isoformat(), start='14:00', end='14:45', reason='Worked offline at home')
        k = 'm:' + r['session']['id']
        self.assertEqual(self.api(self.bm, 'time.approvals.decide', {'ids': [k], 'decision': 'reject'}, method='POST')['code'], 'VALIDATION')
        self.assertEqual(self.api(self.bm, 'time.approvals.decide', {'ids': [k], 'decision': 'reject', 'comment': 'No evidence'}, method='POST')['done'], [k])
        self.assertEqual(W.totals(['t_rahul'])['t_rahul']['actual'], 0)

    def test_x6_auto_stop_at_cutoff(self):
        self.clk.set(17, 0)
        self.act('time.start', taskId='t_rahul')
        self.clk.set(21, 0)
        W.auto_stop_due()
        s = WorkSession.objects.get(task_rid='t_rahul')
        self.assertEqual(s.status, 'auto_stopped')
        self.assertTrue(s.needs_review)
        self.assertEqual(s.ended_at.astimezone(IST).strftime('%H:%M'), '20:30')  # BLR day end 18:30 + 120 min
        self.assertEqual(s.work_seconds, 210 * 60)
        self.assertTrue(NotificationJob.objects.filter(event_key='time.auto_stopped').exists())

    def test_x7_midnight_split(self):
        a = datetime.datetime(2026, 12, 1, 23, 30, tzinfo=IST)
        days = split_days([(a, a + datetime.timedelta(minutes=75))], 'Asia/Kolkata')
        self.assertEqual(days, {datetime.date(2026, 12, 1): 1800, datetime.date(2026, 12, 2): 2700})

    def test_x8_x9_metrics(self):
        self.assertEqual(W.metrics(None, 3600)['efficiency'], None)
        e = 300 * 60 + 120 * 60
        a = 270 * 60 + 150 * 60
        self.assertEqual(round(e / a * 100, 1), 100.0)

    def test_x10_switch_and_single_running_timer(self):
        task('t_rahul2', 'e_rahul')
        s1 = self.act('time.start', taskId='t_rahul')['session']
        self.clk.set(11, 0)
        r = self.act('time.start', taskId='t_rahul2')
        self.assertEqual(r['code'], 'TIMER_ACTIVE')
        r = self.act('time.start', taskId='t_rahul2', switch=True)
        self.assertEqual(r['stopped']['work'], 3600)
        self.assertEqual(r['session']['startedAt'], r['stopped']['endedAt'])
        self.assertEqual(WorkSession.objects.filter(emp_id='e_rahul', status='running').count(), 1)

    def test_replayed_request_is_ignored(self):
        a = self.act('time.start', taskId='t_rahul', reqId='req-1')
        self.clk.set(10, 5)
        b = self.act('time.start', taskId='t_rahul', reqId='req-1')
        self.assertTrue(b.get('replay'))
        self.assertEqual(a['session']['id'], b['session']['id'])
        self.assertEqual(WorkSession.objects.count(), 1)

    def test_invalid_state_and_ownership(self):
        s = self.act('time.start', taskId='t_rahul')['session']
        self.assertEqual(self.act('time.resume', sessionId=s['id'])['code'], 'INVALID_STATE')
        self.assertEqual(self.act('time.start', taskId='t_ho1')['code'], 'NOT_FOUND')
        other = self.api(self.em2, 'time.stop', {'sessionId': s['id']}, method='POST')
        self.assertEqual(other['code'], 'NOT_FOUND')

    def test_done_task_and_leave_block_start(self):
        r = Record.objects.get(coll='tasks', rid='t_rahul')
        r.data = dict(r.data, status='done')
        r.save()
        self.assertEqual(self.act('time.start', taskId='t_rahul')['code'], 'TASK_DONE')
        r.data = dict(r.data, status='todo')
        r.save()
        Record.objects.create(coll='leaves', rid='lv', data={'id': 'lv', 'eid': 'e_rahul', 'kind': 'full', 'from': self.day.isoformat(), 'status': 'Approved'})
        self.assertEqual(self.act('time.start', taskId='t_rahul')['code'], 'ON_LEAVE')

    def test_complete_stops_and_marks_done(self):
        self.act('time.start', taskId='t_rahul')
        self.clk.set(11, 0)
        r = self.act('time.complete', taskId='t_rahul')
        self.assertEqual(r['stopped']['work'], 3600)
        self.assertEqual(Record.objects.get(coll='tasks', rid='t_rahul').data['status'], 'done')
        self.assertTrue(NotificationJob.objects.filter(event_key='task.completed').exists())

    def test_reassign_stops_old_assignees_timer(self):
        self.act('time.start', taskId='t_rahul')
        t = dict(Record.objects.get(coll='tasks', rid='t_rahul').data, eid='e_sneha')
        self.assertEqual(self.save(self.bm, [{'c': 'tasks', 'op': 'set', 'rec': t}])['applied'], 1)
        s = WorkSession.objects.get(task_rid='t_rahul')
        self.assertEqual(s.status, 'stopped')
        self.assertEqual(s.emp_id, 'e_rahul')
        self.assertEqual(Record.objects.get(coll='tasks', rid='t_rahul').data['branchId'], str(self.blr.pk))

    def test_logout_pauses(self):
        c = self.client_for(self.em)
        self.api(c, 'time.start', {'taskId': 't_rahul'}, method='POST')
        c.post('/api/?a=logout', data='{}', content_type='application/json', HTTP_X_REQUESTED_WITH='staffroom')
        self.assertEqual(WorkSession.objects.get(task_rid='t_rahul').status, 'paused')

    def test_task_with_time_cannot_be_deleted(self):
        s = self.act('time.start', taskId='t_rahul')['session']
        self.act('time.stop', sessionId=s['id'])
        self.assertEqual(self.save(self.ca, [{'c': 'tasks', 'op': 'del', 'id': 't_rahul'}])['rejected'], 1)

    def test_stopped_session_cannot_be_changed_through_save(self):
        s = self.act('time.start', taskId='t_rahul')['session']
        self.clk.set(11, 0)
        self.act('time.stop', sessionId=s['id'])
        self.assertEqual(self.act('time.pause', sessionId=s['id'], reason='break')['code'], 'INVALID_STATE')
        self.assertEqual(WorkSession.objects.get(pk=s['id']).work_seconds, 3600)

    def test_adjustment_flow(self):
        s = self.act('time.start', taskId='t_rahul')['session']
        self.clk.set(11, 0)
        self.act('time.stop', sessionId=s['id'])
        self.clk.set(18, 0)
        d = self.day.isoformat()
        r = self.act('time.adjust.create', sessionId=s['id'], newStart=d + 'T10:00', newEnd=d + 'T11:30', reason='Forgot to start the timer on time')
        self.assertIn('adjustment', r, r)
        self.assertEqual(r['adjustment']['newWork'], 5400)
        out = self.api(self.bm, 'time.approvals.decide', {'ids': ['a:' + r['adjustment']['id']], 'decision': 'approve'}, method='POST')
        self.assertTrue(out['done'])
        s2 = WorkSession.objects.get(pk=s['id'])
        self.assertEqual(s2.work_seconds, 5400)
        self.assertEqual(s2.adjustments.first().old_work_seconds, 3600)

    def test_load_includes_task_time(self):
        s = self.act('time.start', taskId='t_rahul')['session']
        self.clk.set(11, 0)
        self.act('time.stop', sessionId=s['id'])
        d = self.load(self.em)
        self.assertEqual(d['taskTime']['t_rahul'][0], 3600)

    def test_task_estimate_validation_in_save(self):
        t = dict(Record.objects.get(coll='tasks', rid='t_rahul').data, est=2)
        self.assertEqual(self.save(self.bm, [{'c': 'tasks', 'op': 'set', 'rec': t}])['rejected'], 1)
        t = dict(t, est=90, start=self.day.isoformat(), dailyMin=60)
        self.assertEqual(self.save(self.bm, [{'c': 'tasks', 'op': 'set', 'rec': t}])['applied'], 1)
