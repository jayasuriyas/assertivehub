"""Three starter blog posts, saved as DRAFTS. Review, edit and publish them from the owner panel (Blog tab)."""
from django.db import migrations

POSTS = [
    ('attendance-without-chasing-anyone', 'Attendance without chasing anyone', 'Guides',
     'How phone check-in, QR codes and biometric machines keep the register up to date on their own.',
     """Most small companies still keep attendance the hard way: someone looks around the office, marks a sheet, and fixes mistakes at the end of the month. It works until the team grows, people start working from client sites, or a second office opens.

## Let the register fill itself

In Assertive Hub, every way of checking in writes to the same daily register:

- **Phone check-in** with an office location radius, a selfie or a QR code at the door. You choose which of these are required.
- **Biometric machines** that send fingerprint or face punches straight to the register.
- **Manual marking** by an admin, for the days when nothing else works.

## Fair late marks

Each branch has its own working hours and grace time. Someone in a branch that starts at 9:30 is not marked late at 9:15, and a shift worker is judged by their own shift.

## Corrections, not arguments

When someone forgets to punch out, they send a correction request with a reason. An admin resolves it, and the change is recorded in the audit log. Nobody edits the register quietly.

## Getting started

Switch on Attendance and Self check-in in Settings, Modules, set your office hours, and share the check-in link with your team. Most companies are running the same day."""),
    ('time-tracking-without-micromanaging', 'Task time tracking without micromanaging', 'Product',
     'Estimates, one-tap timers and approvals that show where time really goes.',
     """Time tracking has a bad name because it is often used to watch people. Used well, it answers a simpler question: did this work take as long as we thought?

## Estimate first

Give each task an estimated time when you assign it. That one number makes everything else useful: variance, efficiency and workload.

## One timer at a time

People press Start work when they begin and Pause when they stop, with a short reason such as a meeting or a client call. Only one timer runs at a time, so a day can never add up to more than 24 hours.

## Honest corrections

Worked offline? Forgot the timer? Add the time manually with a reason. It counts once the manager approves it, and every change keeps its history.

## Look at patterns, not minutes

The useful reports are the ones that show patterns:

- Which kinds of work are always under-estimated
- Who has more planned work than free hours this week
- Which clients cause the most reopens

Use them to plan better, not to count minutes."""),
    ('whatsapp-and-sms-alerts-in-india', 'Setting up WhatsApp and SMS alerts in India', 'Guides',
     'What you need from Meta and the DLT portals before your team gets alerts on their phones.',
     """Alerts on WhatsApp or SMS are one of the most requested features, and also the one with the most paperwork. Here is what to expect.

## WhatsApp Business

You need a Meta Business account, a verified business and a WhatsApp Business phone number. Every message you send first is a template that Meta approves. Approval usually takes from a few minutes to a day.

## SMS and DLT

In India, business SMS must be registered on a DLT portal run by the telecom operators. You register your company, a sender ID (the name people see) and the wording of every message. Plan for one to three weeks.

## Consent comes first

In Assertive Hub, nobody receives a WhatsApp or SMS message until they verify their number and opt in. Anyone can reply STOP to opt out, and quiet hours keep messages out of the night. In-app alerts always work, so nothing is missed while the paperwork is in progress.

## What to send

Start with the messages people actually want: leave decisions, new tasks, meeting reminders and important announcements. Add more only when your team asks for them."""),
]


def seed(apps, schema_editor):
    if schema_editor.connection.alias != 'default':  # platform database only, never a company database
        return
    Post = apps.get_model('saas', 'Post')
    for slug, title, tag, excerpt, body in POSTS:
        Post.objects.get_or_create(slug=slug, defaults={'title': title, 'tag': tag, 'excerpt': excerpt, 'body': body, 'author': 'Assertive Hub team', 'published': False})


class Migration(migrations.Migration):
    dependencies = [('saas', '0002_site')]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
