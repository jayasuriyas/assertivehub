"""Branch master, transfers with effective dates and history, report attribution, module switch, prefs merge."""
import datetime

from core.models import Attendance, Branch, EmployeeBranchAssignment as A, Profile, Record, Transfer
from core.v6 import branches as B, reports

from .helpers import Base

TODAY = datetime.date.today()


class Branches(Base):
    def test_create_and_rules(self):
        b = {'name': 'Hyderabad', 'code': 'hyd', 'workStart': '09:00', 'workEnd': '18:00', 'workDays': [1, 2, 3, 4, 5], 'tz': 'Asia/Kolkata'}
        r = self.api(self.sa, 'branches.save', b, method='POST')
        self.assertTrue(r['ok'], r)
        self.assertEqual(r['branch']['code'], 'HYD')
        self.assertEqual(self.api(self.sa, 'branches.save', dict(b, name='Other'), method='POST')['code'], 'VALIDATION')
        self.assertEqual(self.api(self.ba, 'branches.save', dict(b, code='X1'), method='POST')['code'], 'FORBIDDEN')
        self.assertEqual(self.api(self.sa, 'branches.save', dict(b, code='X1', workEnd='08:00', name='Late'), method='POST')['code'], 'VALIDATION')

    def test_code_immutable_once_used(self):
        r = self.api(self.sa, 'branches.save', {'id': str(self.blr.pk), 'name': 'Bangalore', 'code': 'BLR2', 'workDays': [1, 2, 3, 4, 5]}, method='POST')
        self.assertEqual(r['code'], 'IMMUTABLE')

    def test_deactivate_blocked_with_employees(self):
        r = self.api(self.sa, 'branches.deactivate', {'id': str(self.maa.pk), 'reason': 'Closing'}, method='POST')
        self.assertEqual(r['code'], 'BRANCH_IN_USE')
        self.assertEqual(r['details']['blockers']['employees'], 1)
        self.assertEqual(self.api(self.sa, 'branches.deactivate', {'id': str(self.ho.pk), 'reason': 'Closing'}, method='POST')['code'], 'BRANCH_IN_USE')

    def test_transfer_now_keeps_history_and_attribution(self):
        y = TODAY.replace(day=1)
        if y == TODAY:
            y = TODAY  # first of the month: effective today
        Attendance.objects.create(day=(y - datetime.timedelta(days=3)).isoformat(), eid='e_ho1', data={'s': 'P', 'p': [], 'tl': []})
        r = self.api(self.sa, 'transfers.create', {'empId': 'e_ho1', 'toBranchId': str(self.blr.pk), 'effectiveDate': y.isoformat(), 'reason': 'Team expansion', 'reportsTo': 'e_arjun'}, method='POST')
        self.assertTrue(r['ok'], r)
        self.assertEqual(r['transfer']['status'], 'applied')
        e = Record.objects.get(coll='employees', rid='e_ho1').data
        self.assertEqual((e['branchId'], e['reportsTo']), (str(self.blr.pk), 'e_arjun'))
        rows = list(A.objects.filter(emp_id='e_ho1', kind='primary').order_by('effective_from').values_list('branch__code', 'effective_to', 'status'))
        self.assertEqual(rows[0], ('HO', y - datetime.timedelta(days=1), 'ended'))
        self.assertEqual(rows[1][0], 'BLR')
        per = reports._branch_periods()
        self.assertEqual(reports._branch_on(per, 'e_ho1', y - datetime.timedelta(days=3)), str(self.ho.pk))
        self.assertEqual(reports._branch_on(per, 'e_ho1', TODAY), str(self.blr.pk))

    def test_scheduled_transfer_and_rules(self):
        fut = (TODAY + datetime.timedelta(days=10)).isoformat()
        r = self.api(self.sa, 'transfers.create', {'empId': 'e_ho1', 'toBranchId': str(self.blr.pk), 'effectiveDate': fut, 'reason': 'Move'}, method='POST')
        self.assertEqual(r['transfer']['status'], 'scheduled')
        self.assertEqual(Record.objects.get(coll='employees', rid='e_ho1').data['branchId'], str(self.ho.pk))
        self.assertEqual(self.api(self.sa, 'transfers.create', {'empId': 'e_ho1', 'toBranchId': str(self.maa.pk), 'effectiveDate': fut, 'reason': 'Move'}, method='POST')['code'], 'TRANSFER_EXISTS')
        old = (TODAY.replace(day=1) - datetime.timedelta(days=1)).isoformat()
        self.assertEqual(self.api(self.sa, 'transfers.create', {'empId': 'e_maa1', 'toBranchId': str(self.blr.pk), 'effectiveDate': old, 'reason': 'x'}, method='POST')['code'], 'VALIDATION')
        t = Transfer.objects.get(status='scheduled')
        t.effective_date = TODAY
        t.save()
        B._apply_due({}, None)
        self.assertEqual(Record.objects.get(coll='employees', rid='e_ho1').data['branchId'], str(self.blr.pk))

    def test_hr_needs_both_branches(self):
        r = self.api(self.hr, 'transfers.create', {'empId': 'e_ho1', 'toBranchId': str(self.maa.pk), 'effectiveDate': TODAY.isoformat(), 'reason': 'x'}, method='POST')
        self.assertEqual(r['code'], 'FORBIDDEN')
        r = self.api(self.hr, 'transfers.create', {'empId': 'e_ho1', 'toBranchId': str(self.blr.pk), 'effectiveDate': TODAY.isoformat(), 'reason': 'Needed'}, method='POST')
        self.assertTrue(r['ok'], r)

    def test_branch_hours_drive_late_status(self):
        from core import punch
        rahul = Record.objects.get(coll='employees', rid='e_rahul').data
        start, grace = punch.shift_for('e_rahul', TODAY.isoformat(), rahul)
        self.assertEqual(start, 9 * 60 + 30)  # Bangalore starts at 09:30, company default is 09:00

    def test_module_off_blocks_v6_endpoints(self):
        from core.models import Config
        c = Config.objects.get(key='settings')
        c.value = dict(c.value, modules={})
        c.save()
        self.assertEqual(self.api(self.sa, 'branches.list')['code'], 'MODULE_OFF')
        self.assertEqual(self.api(self.em, 'time.state')['code'], 'MODULE_OFF')

    def test_prefs_merge(self):
        self.api(self.em, 'prefs', {'theme': 'blue', 'mode': 'dark'}, method='POST')
        self.api(self.em, 'prefs', {'dash': {'order': ['meetings', 'tasks'], 'hidden': ['podium']}}, method='POST')
        p = Profile.objects.get(user=self.em).prefs
        self.assertEqual((p['mode'], p['dash']['order']), ('dark', ['meetings', 'tasks']))

    def test_reports_branch_comparison_and_employee_scope(self):
        Attendance.objects.create(day=TODAY.isoformat(), eid='e_rahul', data={'s': 'L', 'p': [], 'tl': [], 'b': str(self.blr.pk)})
        Attendance.objects.create(day=TODAY.isoformat(), eid='e_ho1', data={'s': 'P', 'p': [], 'tl': []})
        r = self.api(self.sa, 'reports.run', {'report': 'R-12'}, method='GET')
        rows = {x['code']: x for x in r['rows']}
        self.assertEqual(rows['BLR']['latePct'], 100.0)
        self.assertEqual(rows['HO']['attendancePct'], 100.0)
        self.assertEqual(self.api(self.ba, 'reports.run', {'report': 'R-12'}, method='GET')['code'], 'FORBIDDEN')
        hr = self.api(self.hr, 'reports.run', {'report': 'R-12'}, method='GET')
        self.assertEqual({x['code'] for x in hr['rows']}, {'HO', 'BLR'})
        csv = self.client_for(self.sa).get('/api/', {'a': 'reports.export', 'report': 'R-12'}, HTTP_X_REQUESTED_WITH='staffroom')
        self.assertEqual(csv['Content-Type'], 'text/csv; charset=utf-8')
        self.assertIn(b'Bangalore', csv.content)

    def test_csv_formula_injection(self):
        self.assertEqual(reports.safe('=HYPERLINK("x")'), '\'=HYPERLINK("x")')
        self.assertEqual(reports.safe('Normal'), 'Normal')

    def test_tick_runs_for_company(self):
        from core.v6.system import each_company, tick
        out = each_company(tick)
        self.assertIn('default', out)
        self.assertNotIn('error', str(out['default'].get('autostop')))
