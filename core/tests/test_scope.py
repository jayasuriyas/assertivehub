"""Branch-level data isolation enforced on the server (TS-B06). Out-of-scope = invisible and unwritable."""
from core.models import Attendance, AuditEvent, Record, UserBranchScope

from .helpers import Base, TODAY, login_user

BLR_EMPS = {'e_arjun', 'e_rahul', 'e_sneha', 'e_kiran'}
HO_EMPS = {'e_boss', 'e_ho1', 'e_hr'}


class LoadIsolation(Base):
    def test_branch_admin_sees_only_branch(self):
        d = self.load(self.ba)
        self.assertEqual({e['id'] for e in d['employees']}, BLR_EMPS)
        self.assertEqual([t['id'] for t in d['tasks']], ['t_rahul'])
        self.assertFalse(d['myScope']['all'])
        self.assertEqual(d['myScope']['branches'], [str(self.blr.pk)])

    def test_branch_admin_does_not_see_salary_of_others(self):
        d = self.load(self.ba)
        others = [e for e in d['employees'] if e['id'] != 'e_kiran']
        self.assertTrue(others and all('ctc' not in e for e in others))

    def test_hr_admin_sees_two_branches_with_salary(self):
        d = self.load(self.hr)
        self.assertEqual({e['id'] for e in d['employees']}, BLR_EMPS | HO_EMPS)
        self.assertTrue(all('ctc' in e for e in d['employees']))
        self.assertNotIn('t_maa1', [t['id'] for t in d['tasks']])

    def test_branch_manager_sees_branch(self):
        d = self.load(self.bm)
        self.assertEqual({e['id'] for e in d['employees']}, BLR_EMPS)

    def test_secondary_access_shows_employee_but_not_attendance(self):
        import datetime
        from core.models import EmployeeBranchAssignment as A
        A.objects.create(emp_id='e_maa1', branch=self.blr, kind='secondary', effective_from=datetime.date(2025, 1, 1), reason='Trainer')
        Attendance.objects.create(day=TODAY, eid='e_maa1', data={'s': 'P', 'p': [], 'tl': []})
        d = self.load(self.ba)
        self.assertIn('e_maa1', {e['id'] for e in d['employees']})
        self.assertNotIn('e_maa1', d['attendance'].get(TODAY, {}))

    def test_attendance_filtered(self):
        Attendance.objects.create(day=TODAY, eid='e_ho1', data={'s': 'P', 'p': [], 'tl': []})
        Attendance.objects.create(day=TODAY, eid='e_rahul', data={'s': 'P', 'p': [], 'tl': []})
        d = self.load(self.ba)
        self.assertEqual(set(d['attendance'][TODAY]), {'e_rahul'})


class WriteIsolation(Base):
    def test_branch_admin_cannot_edit_ho_employee(self):
        e = dict(Record.objects.get(coll='employees', rid='e_ho1').data, designation='Hacked')
        r = self.save(self.ba, [{'c': 'employees', 'op': 'set', 'rec': e}])
        self.assertEqual(r['rejected'], 1)
        self.assertEqual(Record.objects.get(coll='employees', rid='e_ho1').data['designation'], 'Designer')

    def test_branch_admin_can_edit_own_branch_employee(self):
        e = dict(Record.objects.get(coll='employees', rid='e_rahul').data, designation='Senior designer')
        self.assertEqual(self.save(self.ba, [{'c': 'employees', 'op': 'set', 'rec': e}])['applied'], 1)

    def test_branch_admin_cannot_change_salary(self):
        e = dict(Record.objects.get(coll='employees', rid='e_rahul').data, ctc=999999)
        self.save(self.ba, [{'c': 'employees', 'op': 'set', 'rec': e}])
        self.assertEqual(Record.objects.get(coll='employees', rid='e_rahul').data['ctc'], 50000)

    def test_branch_admin_cannot_assign_task_to_other_branch(self):
        t = {'id': 'tnew', 'title': 'x', 'desc': '', 'eid': 'e_ho1', 'cid': '', 'pri': 'med', 'status': 'todo', 'due': TODAY, 'cat': 'Poster', 'qty': 1, 'reopens': [], 'mistakes': []}
        self.assertEqual(self.save(self.ba, [{'c': 'tasks', 'op': 'set', 'rec': t}])['rejected'], 1)
        self.assertEqual(self.save(self.ba, [{'c': 'tasks', 'op': 'set', 'rec': dict(t, eid='e_rahul')}])['applied'], 1)

    def test_branch_admin_cannot_delete_other_branch_task(self):
        self.assertEqual(self.save(self.ba, [{'c': 'tasks', 'op': 'del', 'id': 't_ho1'}])['rejected'], 1)
        self.assertTrue(Record.objects.filter(coll='tasks', rid='t_ho1').exists())

    def test_attendance_out_of_scope_rejected(self):
        op = {'c': 'attendance', 'op': 'set', 'date': TODAY, 'eid': 'e_ho1', 'rec': {'s': 'P', 'p': []}}
        self.assertEqual(self.save(self.ba, [op])['rejected'], 1)
        self.assertEqual(self.save(self.ba, [dict(op, eid='e_rahul')])['applied'], 1)
        self.assertEqual(Attendance.objects.get(day=TODAY, eid='e_rahul').data['b'], str(self.blr.pk))

    def test_leave_approval_out_of_scope_rejected(self):
        Record.objects.create(coll='leaves', rid='lvh', data={'id': 'lvh', 'eid': 'e_ho1', 'kind': 'full', 'from': TODAY, 'status': 'Pending'})
        rec = dict(Record.objects.get(coll='leaves', rid='lvh').data, status='Approved')
        self.assertEqual(self.save(self.ba, [{'c': 'leaves', 'op': 'set', 'rec': rec}])['rejected'], 1)

    def test_company_wide_holiday_needs_company_admin(self):
        ev = {'id': 'ev1', 'title': 'Onam', 'type': 'holiday', 'date': TODAY}
        self.assertEqual(self.save(self.ba, [{'c': 'events', 'op': 'set', 'rec': ev}])['rejected'], 1)
        self.assertEqual(self.save(self.ba, [{'c': 'events', 'op': 'set', 'rec': dict(ev, branchIds=[str(self.blr.pk)])}])['applied'], 1)
        self.assertEqual(self.save(self.ba, [{'c': 'events', 'op': 'set', 'rec': dict(ev, id='ev2', branchIds=[str(self.ho.pk)])}])['rejected'], 1)

    def test_new_employee_goes_to_scoped_admins_branch(self):
        e = {'id': 'e_new', 'name': 'New', 'designation': 'x', 'dept': 'Design', 'status': 'Active', 'joined': TODAY, 'branchId': str(self.ho.pk)}
        self.assertEqual(self.save(self.ba, [{'c': 'employees', 'op': 'set', 'rec': e}])['applied'], 1)
        self.assertEqual(Record.objects.get(coll='employees', rid='e_new').data['branchId'], str(self.blr.pk))

    def test_server_owned_branch_cannot_be_changed_by_save(self):
        e = dict(Record.objects.get(coll='employees', rid='e_ho1').data, branchId=str(self.maa.pk))
        self.save(self.sa, [{'c': 'employees', 'op': 'set', 'rec': e}])
        self.assertEqual(Record.objects.get(coll='employees', rid='e_ho1').data['branchId'], str(self.ho.pk))
        e.pop('branchId')  # an old browser tab that does not know the field
        self.save(self.sa, [{'c': 'employees', 'op': 'set', 'rec': e}])
        self.assertEqual(Record.objects.get(coll='employees', rid='e_ho1').data['branchId'], str(self.ho.pk))


class OtherEndpoints(Base):
    def test_users_list_scoped(self):
        r = self.api(self.ba, 'users')
        names = {x['user'] for x in r['users']}
        self.assertIn('rahul', names)
        self.assertNotIn('hari', names)

    def test_scoped_admin_cannot_create_login_for_other_branch(self):
        r = self.api(self.ba, 'user_save', {'user': 'newlogin', 'role': 'employee', 'empId': 'e_ho1', 'pass': 'password123', 'active': True}, method='POST')
        self.assertEqual(r['_status'], 403)

    def test_scoped_admin_cannot_create_admin(self):
        r = self.api(self.ba, 'user_save', {'user': 'newadmin', 'role': 'branchadmin', 'empId': 'e_sneha', 'pass': 'password123', 'active': True, 'branches': [str(self.blr.pk)]}, method='POST')
        self.assertEqual(r['_status'], 403)

    def test_super_admin_creates_branch_admin_with_branches(self):
        r = self.api(self.sa, 'user_save', {'user': 'newba', 'role': 'branchadmin', 'empId': 'e_maa1', 'pass': 'password123', 'active': True, 'branches': [str(self.maa.pk)]}, method='POST')
        self.assertTrue(r['ok'], r)
        self.assertEqual(r['user']['branches'], [str(self.maa.pk)])
        from .helpers import emp
        emp('e_free', 'Free', self.maa)
        r = self.api(self.sa, 'user_save', {'user': 'newba2', 'role': 'branchadmin', 'empId': 'e_free', 'pass': 'password123', 'active': True}, method='POST')
        self.assertEqual(r['_status'], 400)

    def test_selfie_out_of_scope(self):
        r = self.api(self.ba, 'selfie', {'date': TODAY, 'eid': 'e_ho1', 'time': '09:00'}, method='POST')
        self.assertEqual(r['_status'], 404)

    def test_audit_scoped(self):
        AuditEvent.objects.create(action='x', entity_type='employee', entity_id='e_ho1', branch=self.ho)
        AuditEvent.objects.create(action='y', entity_type='employee', entity_id='e_rahul', branch=self.blr)
        log = self.api(self.ba, 'audit')['log']
        self.assertEqual([r[3] for r in log], ['y'])

    def test_v6_meeting_with_out_of_scope_participant_is_not_found(self):
        import datetime
        d = (datetime.date.today() + datetime.timedelta(days=3)).isoformat()
        r = self.api(self.ba, 'meetings.save', {'title': 'Sync', 'category': 'direct', 'date': d, 'start': '11:00', 'end': '11:30', 'participants': [{'empId': 'e_ho1'}]}, method='POST')
        self.assertEqual(r['_status'], 404)
        self.assertEqual(r['code'], 'NOT_FOUND')
