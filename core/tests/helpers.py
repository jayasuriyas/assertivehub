"""Shared fixtures: a company with HO + BLR + MAA, employees in each, logins for every role."""
import datetime
import json

from django.contrib.auth.models import User
from django.test import TestCase, Client

from core.models import Branch, Config, Profile, Record, UserBranchScope, EmployeeBranchAssignment

TODAY = datetime.date.today().isoformat()


def emp(rid, name, branch=None, reports_to='', dept='Design', **kw):
    d = {'id': rid, 'name': name, 'designation': 'Designer', 'dept': dept, 'email': '%s@example.com' % rid, 'phone': '98470%05d' % (abs(hash(rid)) % 100000),
         'joined': '2025-01-01', 'status': 'Active', 'empId': rid.upper(), 'reportsTo': reports_to, 'ctc': 50000}
    if branch is not None:
        d['branchId'] = str(branch.pk)
    d.update(kw)
    Record.objects.create(coll='employees', rid=rid, data=d)
    if branch is not None:
        EmployeeBranchAssignment.objects.create(emp_id=rid, branch=branch, kind='primary', effective_from=datetime.date(2025, 1, 1), reason='Initial assignment')
    return d


def task(rid, eid, **kw):
    d = {'id': rid, 'title': 'Task ' + rid, 'desc': '', 'eid': eid, 'cid': '', 'pri': 'med', 'status': 'todo', 'due': TODAY, 'cat': 'Poster', 'qty': 1, 'reopens': [], 'mistakes': []}
    d.update(kw)
    Record.objects.create(coll='tasks', rid=rid, data=d)
    return d


def login_user(username, role, emp_id='', branches=()):
    u = User.objects.create_user(username, password='password123')
    Profile.objects.create(user=u, role=role, emp_id=emp_id)
    for b in branches:
        UserBranchScope.objects.create(user=u, branch=b)
    return u


class Base(TestCase):
    modules = ('branches', 'notify', 'meetings', 'timetrack')

    @classmethod
    def setUpTestData(cls):
        Config.objects.create(key='company', value='Acme')
        Config.objects.create(key='settings', value={'workStart': '09:00', 'workEnd': '18:00', 'workDays': [1, 2, 3, 4, 5, 6],
                                                     'modules': {m: True for m in cls.modules}})
        Config.objects.create(key='rev', value=1)
        cls.ho = Branch.objects.get(is_head_office=True)  # created by migration 0003
        cls.ho.work_days = [1, 2, 3, 4, 5, 6]
        cls.ho.save()
        cls.blr = Branch.objects.create(code='BLR', name='Bangalore', work_start='09:30', work_end='18:30', work_days=[1, 2, 3, 4, 5, 6])
        cls.maa = Branch.objects.create(code='MAA', name='Chennai', work_days=[1, 2, 3, 4, 5, 6])
        emp('e_boss', 'Priya', cls.ho, dept='Management')
        emp('e_ho1', 'Hari', cls.ho, reports_to='e_boss')
        emp('e_arjun', 'Arjun', cls.blr, reports_to='e_boss')
        emp('e_rahul', 'Rahul', cls.blr, reports_to='e_arjun')
        emp('e_sneha', 'Sneha', cls.blr, reports_to='e_arjun')
        emp('e_kiran', 'Kiran', cls.blr)
        emp('e_maa1', 'Meena', cls.maa)
        emp('e_hr', 'Meera', cls.ho, dept='HR')
        task('t_rahul', 'e_rahul')
        task('t_ho1', 'e_ho1')
        task('t_maa1', 'e_maa1')
        cls.sa = login_user('sa', 'superadmin', 'e_boss')
        cls.ca = login_user('ca', 'admin')
        cls.hr = login_user('hr', 'hradmin', 'e_hr', [cls.ho, cls.blr])
        cls.ba = login_user('ba', 'branchadmin', 'e_kiran', [cls.blr])
        cls.bm = login_user('bm', 'branchmgr', 'e_arjun', [cls.blr])
        cls.mg = login_user('mg', 'manager', 'e_arjun_mgr_unused')
        cls.em = login_user('rahul', 'employee', 'e_rahul')
        cls.em2 = login_user('sneha', 'employee', 'e_sneha')
        cls.ho_emp = login_user('hari', 'employee', 'e_ho1')

    def client_for(self, u):
        c = Client()
        c.force_login(u)
        return c

    def api(self, u, action, body=None, method=None):
        c = self.client_for(u) if not isinstance(u, Client) else u
        h = {'HTTP_X_REQUESTED_WITH': 'staffroom'}
        if body is None and method != 'POST':
            r = c.get('/api/', {'a': action}, **h)
        elif method == 'GET':
            q = {'a': action}
            q.update(body)
            r = c.get('/api/', q, **h)
        else:
            r = c.post('/api/?a=' + action, data=json.dumps(body or {}), content_type='application/json', **h)
        try:
            j = r.json()
        except ValueError:
            j = {'_raw': r.content}
        j['_status'] = r.status_code
        return j

    def load(self, u):
        return self.api(u, 'load')['data']

    def save(self, u, ops, base_rev=None):
        c = self.client_for(u)
        r = c.post('/api/?a=save', data=json.dumps({'ops': ops}), content_type='application/json', HTTP_X_REQUESTED_WITH='staffroom', HTTP_X_BASE_REV=str(base_rev or 1))
        return r.json()
