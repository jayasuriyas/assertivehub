"""Characterisation tests: behaviour of the four original roles must not change with v6 (TS-D05)."""
from core.models import Attendance, Record

from .helpers import Base, TODAY, login_user


class LegacyRoles(Base):
    def test_super_and_company_admin_see_everyone(self):
        for u in (self.sa, self.ca):
            d = self.load(u)
            self.assertEqual(len(d['employees']), 8)
            self.assertEqual(len(d['tasks']), 3)

    def test_manager_sees_direct_reports_only(self):
        mgr = login_user('mgr2', 'manager', 'e_boss2')
        Record.objects.create(coll='employees', rid='e_boss2', data={'id': 'e_boss2', 'name': 'Boss2', 'status': 'Active'})
        r = Record.objects.get(coll='employees', rid='e_ho1')
        r.data = dict(r.data, reportsTo='e_boss2')
        r.save()
        d = self.load(mgr)
        self.assertEqual(sorted(e['id'] for e in d['employees']), ['e_boss2', 'e_ho1'])
        self.assertEqual([t['id'] for t in d['tasks']], ['t_ho1'])

    def test_employee_sees_only_self_and_no_salary_of_others(self):
        d = self.load(self.em)
        self.assertEqual([e['id'] for e in d['employees']], ['e_rahul'])
        self.assertEqual([t['id'] for t in d['tasks']], ['t_rahul'])
        self.assertIn('ctc', d['employees'][0])

    def test_employee_can_only_change_task_status(self):
        t = dict(Record.objects.get(coll='tasks', rid='t_rahul').data, title='hacked', status='doing')
        r = self.save(self.em, [{'c': 'tasks', 'op': 'set', 'rec': t}])
        self.assertEqual(r['applied'], 1)
        d = Record.objects.get(coll='tasks', rid='t_rahul').data
        self.assertEqual(d['status'], 'doing')
        self.assertNotEqual(d['title'], 'hacked')

    def test_employee_cannot_touch_others_task(self):
        t = dict(Record.objects.get(coll='tasks', rid='t_ho1').data, status='done')
        r = self.save(self.em, [{'c': 'tasks', 'op': 'set', 'rec': t}])
        self.assertEqual(r['rejected'], 1)

    def test_cannot_approve_own_leave(self):
        lv = {'id': 'lv1', 'eid': 'e_rahul', 'kind': 'full', 'from': TODAY, 'to': TODAY, 'status': 'Pending', 'reason': 'x'}
        self.assertEqual(self.save(self.em, [{'c': 'leaves', 'op': 'set', 'rec': lv}])['applied'], 1)
        self.assertEqual(self.save(self.em, [{'c': 'leaves', 'op': 'set', 'rec': dict(lv, status='Approved')}])['rejected'], 1)
        self.assertEqual(self.save(self.ca, [{'c': 'leaves', 'op': 'set', 'rec': dict(lv, status='Approved')}])['applied'], 1)
        self.assertEqual(Record.objects.get(coll='leaves', rid='lv1').data['status'], 'Approved')

    def test_admin_marks_attendance(self):
        r = self.save(self.ca, [{'c': 'attendance', 'op': 'set', 'date': TODAY, 'eid': 'e_ho1', 'rec': {'s': 'P', 'p': [['09:00', '18:00']]}}])
        self.assertEqual(r['applied'], 1)
        self.assertEqual(Attendance.objects.get(day=TODAY, eid='e_ho1').data['s'], 'P')

    def test_only_super_admin_changes_settings(self):
        self.assertEqual(self.save(self.ca, [{'c': 'settings', 'op': 'set', 'val': {'x': 1}}])['rejected'], 1)
        self.assertEqual(self.save(self.sa, [{'c': 'settings', 'op': 'set', 'val': {'workStart': '09:00', 'modules': {}}}])['applied'], 1)

    def test_api_requires_header_and_post(self):
        c = self.client_for(self.sa)
        self.assertEqual(c.get('/api/?a=load').status_code, 400)
        r = c.get('/api/', {'a': 'save'}, HTTP_X_REQUESTED_WITH='staffroom')
        self.assertEqual(r.status_code, 405)
