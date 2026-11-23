"""Meetings: validation, conflicts, recurrence, RSVP, cancel, minutes, action items -> tasks, visibility, .ics."""
import datetime

from django.core import mail

from core.models import Job, Meeting, MeetingActionItem, MeetingRoom, NotificationDelivery, NotificationJob, Record
from core.v6 import calsync, jobs

from .helpers import Base

D3 = (datetime.date.today() + datetime.timedelta(days=3))
while D3.isoweekday() == 7:
    D3 += datetime.timedelta(days=1)
DAY = D3.isoformat()


class Meetings(Base):
    def setUp(self):
        self.room = MeetingRoom.objects.create(branch=self.blr, name='BLR-Board', capacity=8)

    def mk(self, u=None, **kw):
        b = {'title': 'Sprint sync', 'category': 'team', 'departments': ['Design'], 'date': DAY, 'start': '15:00', 'end': '15:30', 'roomId': str(self.room.pk),
             'participants': [{'empId': 'e_rahul'}, {'empId': 'e_sneha'}], 'reminders': [1440, 15]}
        b.update(kw)
        return self.api(u or self.bm, 'meetings.save', b, method='POST')

    def test_create_invites_and_schedules_reminders(self):
        r = self.mk()
        self.assertTrue(r['ok'], r)
        m = Meeting.objects.get(pk=r['meeting']['id'])
        self.assertEqual(m.branch_id, self.blr.pk)
        self.assertEqual(m.participants.count(), 3)
        inv = NotificationJob.objects.get(event_key='meeting.invitation')
        self.assertEqual(sorted(inv.recipients), ['e_rahul', 'e_sneha'])
        self.assertEqual(NotificationDelivery.objects.filter(job=inv, channel='inapp').count(), 2)
        self.assertEqual(Job.objects.filter(kind='meetings.remind', status='queued').count(), 2)
        self.assertTrue(Job.objects.filter(kind='calsync.push').exists())

    def test_validation(self):
        self.assertEqual(self.mk(end='14:00')['code'], 'VALIDATION')
        self.assertEqual(self.mk(category='online', locationType='online', platform='zoom', joinUrl='http://x')['code'], 'VALIDATION')
        self.assertEqual(self.mk(category='other')['code'], 'VALIDATION')
        self.assertEqual(self.mk(date=(datetime.date.today() - datetime.timedelta(days=1)).isoformat())['code'], 'VALIDATION')
        self.assertTrue(self.mk(date=(datetime.date.today() - datetime.timedelta(days=1)).isoformat(), recordOnly=True, roomId='')['ok'])
        self.assertEqual(self.mk(category='client', departments=[])['code'], 'VALIDATION')

    def test_room_conflict_blocks_and_touching_is_fine(self):
        self.assertTrue(self.mk()['ok'])
        r = self.mk(start='15:15', end='16:00', participants=[{'empId': 'e_kiran'}])
        self.assertEqual(r['code'], 'CONFLICT_ROOM')
        self.assertTrue(self.mk(start='15:30', end='16:00', participants=[{'empId': 'e_kiran'}])['ok'])

    def test_person_conflict_warns_then_allows(self):
        self.assertTrue(self.mk(roomId='')['ok'])
        r = self.mk(roomId='', title='Interview', category='interview', departments=[], start='15:10', end='15:40', participants=[{'empId': 'e_rahul'}])
        self.assertEqual(r['code'], 'CONFLICTS')
        self.assertTrue(any(c['empId'] == 'e_rahul' and c['type'] == 'meeting' for c in r['details']['conflicts']))
        r = self.mk(roomId='', title='Interview', category='interview', departments=[], start='15:10', end='15:40', participants=[{'empId': 'e_rahul'}], confirmConflicts=True)
        self.assertTrue(r['ok'])
        self.assertEqual(r['meeting']['visibility'], 'private')

    def test_weekly_series_one_invite(self):
        r = self.mk(recurrence={'freq': 'weekly', 'count': 6, 'byweekday': [D3.isoweekday() % 7]}, roomId='')
        self.assertEqual(r['count'], 6)
        self.assertEqual(Meeting.objects.count(), 6)
        self.assertEqual(NotificationJob.objects.filter(event_key='meeting.invitation').count(), 1)

    def test_edit_material_change_notifies_and_reschedules(self):
        r = self.mk()
        mid = r['meeting']['id']
        r = self.mk(id=mid, start='16:00', end='16:30')
        self.assertTrue(r['ok'], r)
        self.assertEqual(r['meeting']['sequence'], 1)
        self.assertTrue(NotificationJob.objects.filter(event_key='meeting.updated').exists())
        self.assertEqual(Job.objects.filter(kind='meetings.remind', status='queued').count(), 2)
        self.assertEqual(Job.objects.filter(kind='meetings.remind', status='cancelled').count(), 2)

    def test_only_organizer_edits_participant_rsvps(self):
        mid = self.mk()['meeting']['id']
        self.assertEqual(self.api(self.em, 'meetings.save', {'id': mid, 'title': 'x'}, method='POST')['code'], 'FORBIDDEN')
        r = self.api(self.em, 'meetings.rsvp', {'id': mid, 'response': 'declined'}, method='POST')
        self.assertEqual(r['meeting']['myRsvp'], 'declined')

    def test_cancel(self):
        mid = self.mk()['meeting']['id']
        r = self.api(self.bm, 'meetings.cancel', {'id': mid, 'reason': 'Client moved'}, method='POST')
        self.assertEqual(r['count'], 1)
        self.assertEqual(Job.objects.filter(kind='meetings.remind', status='queued').count(), 0)
        self.assertTrue(NotificationJob.objects.filter(event_key='meeting.cancelled').exists())

    def test_visibility(self):
        mid = self.mk(category='one_to_one', departments=[], participants=[{'empId': 'e_rahul'}], roomId='')['meeting']['id']
        self.assertEqual(self.api(self.em2, 'meetings.get', {'id': mid}, method='GET')['_status'], 404)
        self.assertEqual(self.api(self.ca, 'meetings.get', {'id': mid}, method='GET')['meeting']['title'], 'Sprint sync')
        self.assertEqual(self.api(self.ho_emp, 'meetings.get', {'id': mid}, method='GET')['_status'], 404)

    def test_minutes_and_action_items_to_tasks(self):
        r = self.mk(date=(datetime.date.today() - datetime.timedelta(days=1)).isoformat(), recordOnly=True, roomId='')
        mid = r['meeting']['id']
        r = self.api(self.bm, 'meetings.minutes.save', {'meetingId': mid, 'summary': 'Went well', 'decisions': ['Ship Friday'], 'publish': True}, method='POST')
        self.assertEqual(r['meeting']['minutes']['status'], 'published')
        self.assertEqual(r['meeting']['status'], 'completed')
        r = self.api(self.bm, 'meetings.actions.save', {'meetingId': mid, 'items': [{'title': 'Fix header', 'assignee': 'e_rahul', 'due': DAY, 'estMin': 90}]}, method='POST')
        iid = r['meeting']['actions'][0]['id']
        a = self.api(self.bm, 'meetings.actions.toTasks', {'meetingId': mid, 'itemIds': [iid]}, method='POST')
        b = self.api(self.bm, 'meetings.actions.toTasks', {'meetingId': mid, 'itemIds': [iid]}, method='POST')
        self.assertEqual(a['created'][0]['taskId'], b['created'][0]['taskId'])
        t = Record.objects.get(coll='tasks', rid=a['created'][0]['taskId']).data
        self.assertEqual((t['eid'], t['est'], t['src']['type'], t['branchId']), ('e_rahul', 90, 'meeting', str(self.blr.pk)))
        self.assertEqual(Record.objects.filter(coll='tasks', data__src__type='meeting').count(), 1)

    def test_employee_organizer_cannot_create_tasks(self):
        r = self.api(self.em, 'meetings.save', {'title': 'Pair session', 'category': 'direct', 'date': (datetime.date.today() - datetime.timedelta(days=1)).isoformat(),
                                                 'start': '10:00', 'end': '10:30', 'recordOnly': True, 'participants': [{'empId': 'e_sneha'}]}, method='POST')
        mid = r['meeting']['id']
        r = self.api(self.em, 'meetings.actions.save', {'meetingId': mid, 'items': [{'title': 'Do it', 'assignee': 'e_sneha', 'due': DAY}]}, method='POST')
        a = self.api(self.em, 'meetings.actions.toTasks', {'meetingId': mid, 'itemIds': [r['meeting']['actions'][0]['id']]}, method='POST')
        self.assertEqual(a['created'], [])
        self.assertEqual(len(a['refused']), 1)

    def test_widget_and_lists(self):
        self.mk()
        w = self.api(self.em, 'meetings.widget', {'tab': 'upcoming'}, method='GET')
        self.assertEqual(w['counts']['upcoming'], 1)
        self.assertEqual(w['items'][0]['title'], 'Sprint sync')
        lst = self.api(self.ba, 'meetings.list', {'view': 'all'}, method='GET')
        self.assertEqual(lst['total'], 1)
        self.assertEqual(self.api(self.ho_emp, 'meetings.list', {'view': 'all'}, method='GET')['total'], 0)

    def test_ics_invite_sent_when_organizer_not_connected(self):
        mid = self.mk()['meeting']['id']
        mail.outbox = []
        jobs.run_due()
        self.assertTrue(len(mail.outbox) >= 2)
        att = mail.outbox[0].attachments[0]
        body = att[1] if isinstance(att, tuple) else att.content
        self.assertIn('METHOD:REQUEST', body)
        m = Meeting.objects.get(pk=mid)
        self.assertIn('UID:mtg-%s@' % m.uid, body)
        self.api(self.bm, 'meetings.cancel', {'id': mid, 'reason': 'Moved'}, method='POST')
        mail.outbox = []
        jobs.run_due()
        att = mail.outbox[0].attachments[0]
        body = att[1] if isinstance(att, tuple) else att.content
        self.assertIn('METHOD:CANCEL', body)
        self.assertIn('SEQUENCE:1', body)

    def test_ics_folding_and_escaping(self):
        m = Meeting.objects.create(title='Review; Q4, plan\\x ' + 'long ' * 30, category='team', start_at=datetime.datetime(2026, 12, 3, 9, 30, tzinfo=datetime.timezone.utc),
                                   end_at=datetime.datetime(2026, 12, 3, 10, 0, tzinfo=datetime.timezone.utc), branch=self.blr, organizer_emp='e_arjun')
        text = calsync.ics(m, attendees=[], organizer={'name': 'A', 'email': 'a@x.in'})
        self.assertIn('SUMMARY:Review\\; Q4\\, plan\\\\x', text)
        self.assertTrue(all(len(line.encode()) <= 75 for line in text.split('\r\n')))
