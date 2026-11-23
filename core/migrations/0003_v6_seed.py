"""v6 data: create the Head Office from the company's settings and assign every existing employee to it,
then build the department and designation lists from the values already in use. Safe to run twice."""
import datetime
import re

from django.conf import settings
from django.db import migrations


def forwards(apps, schema_editor):
    db = schema_editor.connection.alias
    Record = apps.get_model('core', 'Record')
    Config = apps.get_model('core', 'Config')
    Attendance = apps.get_model('core', 'Attendance')
    Branch = apps.get_model('core', 'Branch')
    A = apps.get_model('core', 'EmployeeBranchAssignment')
    Department = apps.get_model('core', 'Department')
    Designation = apps.get_model('core', 'Designation')
    BD = apps.get_model('core', 'BranchDepartment')

    ho = Branch.objects.using(db).filter(is_head_office=True).first()
    if ho is None:
        c = Config.objects.using(db).filter(key='settings').first()
        st = c.value if c and isinstance(c.value, dict) else {}
        days = st.get('workDays') if isinstance(st.get('workDays'), list) else [1, 2, 3, 4, 5]
        ho = Branch.objects.using(db).create(code='HO', name='Head Office', is_head_office=True, timezone=settings.TIME_ZONE,
                                             work_start=st.get('workStart') or '09:00', work_end=st.get('workEnd') or '18:00',
                                             break_min=int(st.get('breakMin') if st.get('breakMin') is not None else 60), work_days=days)
    depts, desigs = {}, {}
    for r in Record.objects.using(db).filter(coll='employees'):
        d = dict(r.data or {})
        if not A.objects.using(db).filter(emp_id=r.rid, kind='primary').exists():
            start = None
            if isinstance(d.get('joined'), str) and re.match(r'^\d{4}-\d{2}-\d{2}$', d['joined']):
                start = datetime.date.fromisoformat(d['joined'])
            if start is None:
                first = Attendance.objects.using(db).filter(eid=r.rid).order_by('day').values_list('day', flat=True).first()
                start = datetime.date.fromisoformat(first) if first else datetime.date.today()
            A.objects.using(db).create(emp_id=r.rid, branch=ho, kind='primary', effective_from=start, dept=str(d.get('dept', ''))[:80],
                                       designation=str(d.get('designation', ''))[:80], reports_to=str(d.get('reportsTo', ''))[:60], reason='Initial assignment')
        if not d.get('branchId'):
            d['branchId'] = str(ho.pk)
            r.data = d
            r.save(update_fields=['data'])
        for src, bag in ((d.get('dept'), depts), (d.get('designation'), desigs)):
            v = str(src or '').strip()
            if v:
                bag.setdefault(v.lower(), {}).setdefault(v, 0)
                bag[v.lower()][v] += 1
    # one entry per name, case-insensitive; the most used spelling wins and employee records are aligned to it
    canon = {}
    for low, spellings in depts.items():
        best = max(spellings.items(), key=lambda x: x[1])[0]
        canon[low] = best
        dep, _ = Department.objects.using(db).get_or_create(name=best)
        BD.objects.using(db).get_or_create(branch=ho, department=dep)
    for low, spellings in desigs.items():
        Designation.objects.using(db).get_or_create(name=max(spellings.items(), key=lambda x: x[1])[0])
    for r in Record.objects.using(db).filter(coll='employees'):
        v = str((r.data or {}).get('dept') or '').strip()
        if v and canon.get(v.lower()) and canon[v.lower()] != r.data.get('dept'):
            r.data = dict(r.data, dept=canon[v.lower()])
            r.save(update_fields=['data'])


def backwards(apps, schema_editor):
    """Leaves employee records as they are (branchId is ignored by older code)."""


class Migration(migrations.Migration):
    dependencies = [('core', '0002_v6_schema')]
    operations = [migrations.RunPython(forwards, backwards)]
