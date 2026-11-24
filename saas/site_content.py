"""Words on the public website. Edit this file to change product text, FAQs and testimonials.
Plan names, prices and which plan has which module come from plans.py, so they never go out of step."""

# Module ids (as used by plans.py) -> name shown on the website
MODULE_LABEL = {
    'attendance': 'Attendance register and corrections', 'leave': 'Leave requests and approvals', 'tasks': 'Tasks, reopens and client mistakes',
    'clients': 'Clients, requirements and payments', 'calendar': 'Company calendar and holidays', 'performance': 'Performance scores and leaderboard',
    'review': 'Monthly review', 'workrep': 'Work reports with CSV download', 'ratings': 'Monthly peer ratings', 'meetings': 'Meetings, minutes and action items',
    'timetrack': 'Task time tracking and approvals', 'notices': 'Notice board', 'documents': 'Employee documents', 'assets': 'Asset tracking',
    'checklists': 'Onboarding and exit checklists', 'rota': 'Shift rota', 'checkin': 'Phone check-in with GPS, selfie or QR', 'notify': 'WhatsApp and SMS alerts',
    'calsync': 'Google and Outlook calendar sync', 'branches': 'Multiple branches and transfers', 'payroll': 'Payroll and payslips (PF, ESI, PT)',
    'recruitment': 'Recruitment pipeline', 'invoices': 'Client invoices', 'biometric': 'Biometric attendance machines',
}

# Product areas: each gets a detail page at /products/<key>
AREAS = [
    {'key': 'attendance', 'icon': 'clock', 'title': 'Attendance and check-in',
     'short': 'A daily register that fills itself from phones, QR codes or biometric machines.',
     'intro': 'Know who is in, who is late and who is on leave without chasing anyone. Staff check in from their phone or a fingerprint machine, and corrections go through a simple request-and-approve flow.',
     'modules': ['attendance', 'checkin', 'biometric', 'rota'],
     'points': [('Phone check-in', 'Check in with location radius, a selfie or a QR code at the door. You choose which ones are needed.'),
                ('Biometric machines', 'Fingerprint and face machines send punches straight into the register.'),
                ('Late and early marks', 'Each branch has its own working hours and grace time, so late marks are always fair.'),
                ('Corrections with approval', 'Forgot to punch? Staff send a correction request and an admin resolves it. Every change is logged.'),
                ('Shift rota', 'Different shifts for different people, planned week by week.')]},
    {'key': 'leave', 'icon': 'plane', 'title': 'Leave and holidays',
     'short': 'Full day, half day and hourly leave with clear approvals.',
     'intro': 'Employees ask for leave in a few taps and see their balance. Managers approve their own team; admins can approve anyone. Approved leave shows up in attendance automatically.',
     'modules': ['leave', 'calendar'],
     'points': [('Every kind of leave', 'Full days, half days (first or second half) and one- or two-hour permissions.'),
                ('Fair approvals', 'Nobody approves their own request. A manager\'s leave goes to an admin.'),
                ('Holiday calendar', 'Company holidays, celebrations and important dates, per branch when needed.'),
                ('Automatic attendance', 'Approved days are marked On leave in the register, so nothing is entered twice.')]},
    {'key': 'tasks', 'icon': 'check', 'title': 'Tasks and time tracking',
     'short': 'Assign work, track real time against estimates and see who is overloaded.',
     'intro': 'Give every piece of work an owner, a due date and an estimate. People start a timer when they begin, pause it when they stop, and managers see estimated against actual time without micromanaging.',
     'modules': ['tasks', 'timetrack', 'clients', 'workrep'],
     'points': [('One-tap timer', 'Start, pause with a reason, resume and stop. Only one timer runs at a time, so totals are always honest.'),
                ('Estimates vs actual', 'See variance and efficiency per task, person and client.'),
                ('Manual time with approval', 'Worked offline or forgot the timer? Add time with a reason; the manager approves it.'),
                ('Workload view', 'Planned hours against free hours for the next few days, so nobody is silently overloaded.'),
                ('Client work', 'Tasks linked to clients, with reopens and client-reported mistakes tracked.')]},
    {'key': 'meetings', 'icon': 'calendar', 'title': 'Meetings and calendar',
     'short': 'Plan meetings, send invitations, take minutes and turn decisions into tasks.',
     'intro': 'Schedule team, client and one-to-one meetings with conflict checks. Everyone gets an invitation that works in any calendar, and action items become real tasks with owners and due dates.',
     'modules': ['meetings', 'calsync', 'calendar'],
     'points': [('Conflict check', 'See who is busy, on leave or in another meeting before you send the invite.'),
                ('Invitations that just work', 'Calendar invites by e-mail, plus optional two-way sync with Google Calendar and Outlook.'),
                ('Reminders', 'A day, an hour or minutes before, in the app and by WhatsApp or SMS if switched on.'),
                ('Minutes and action items', 'Write minutes, publish them to attendees and create tasks from action items in one click.'),
                ('Repeating meetings', 'Daily, weekly or monthly series, with changes to one meeting or the whole series.')]},
    {'key': 'performance', 'icon': 'trophy', 'title': 'Performance and reviews',
     'short': 'Scores, a leaderboard, monthly reviews and peer ratings.',
     'intro': 'Performance built from real data: attendance, work done, quality and peer feedback. Monthly reviews take minutes because the numbers are already there.',
     'modules': ['performance', 'review', 'ratings'],
     'points': [('Score from real work', 'Attendance, tasks done, reopens and mistakes add up to one clear score.'),
                ('Leaderboard', 'Celebrate top performers on the dashboard.'),
                ('Monthly review', 'Attendance, work and quality totals for each person, month by month.'),
                ('Peer ratings', 'Everyone rates everyone once a month; individual ratings stay anonymous.')]},
    {'key': 'payroll', 'icon': 'wallet', 'title': 'Payroll and HR records',
     'short': 'Payslips with PF, ESI and professional tax, plus documents, assets and onboarding.',
     'intro': 'Monthly salary with loss of pay taken from attendance, Indian statutory deductions and printable payslips. Keep every offer letter, ID proof and laptop in one place.',
     'modules': ['payroll', 'documents', 'assets', 'checklists', 'recruitment'],
     'points': [('Payroll for India', 'PF, ESI and professional tax rules, with loss of pay from the attendance register.'),
                ('Payslips', 'Printable payslips that employees can see in their own login.'),
                ('Documents', 'Offer letters, ID proofs and contracts stored per employee.'),
                ('Assets', 'Know which laptop or phone is with whom.'),
                ('Onboarding and exit', 'Checklists so nothing is missed when people join or leave.'),
                ('Recruitment', 'Candidates by stage, from application to offer.')]},
    {'key': 'branches', 'icon': 'building', 'title': 'Branches and alerts',
     'short': 'Run several offices from one account, with WhatsApp and SMS alerts.',
     'intro': 'Each branch has its own hours, holidays, admins and reports, while head office sees everything. People get the alerts that matter on WhatsApp or SMS, only after they opt in.',
     'modules': ['branches', 'notify', 'notices'],
     'points': [('Branch admins', 'HR admins, branch admins and branch managers see only the branches they look after.'),
                ('Transfers with history', 'Move people between branches on a date; past records stay with the old branch.'),
                ('WhatsApp and SMS', 'Leave decisions, new tasks, meeting reminders and announcements, with consent and quiet hours.'),
                ('Notice board', 'Announcements for everyone or for chosen branches.'),
                ('Branch reports', 'Compare attendance, lateness and workload across branches.')]},
    {'key': 'clients', 'icon': 'briefcase', 'title': 'Clients and invoices',
     'short': 'Client requirements, payments and GST-ready invoices.',
     'intro': 'Keep each client\'s requirements, work and payments together, and send invoices from the same place your team works.',
     'modules': ['clients', 'invoices'],
     'points': [('Client records', 'Requirements, quantities, due dates and payment status per client.'),
                ('Payments', 'Track paid, part-paid and pending amounts.'),
                ('Invoices', 'Client invoices with a clean print view.'),
                ('Quality tracking', 'Reopens and client-reported mistakes show where work needs attention.')]},
]

# Short points for the home page "why" section (all true for the product)
WHY = [
    ('shield', 'Private by design', 'Every company gets its own separate database. No company can ever see another company\'s data.'),
    ('map', 'Made for India', 'Prices in rupees with GST invoices, PF/ESI/PT payroll rules and Indian mobile numbers.'),
    ('phone', 'Phone and desktop', 'Works in any browser. Staff can check in, ask for leave and run timers from their phone.'),
    ('toggle', 'Only what you need', 'Switch modules on or off for the whole company. Your team sees a simple screen.'),
]

# Connected services shown in the "works with" section (plain names, no logos)
CONNECTS = [('WhatsApp', 'chat'), ('SMS', 'phone'), ('Google Calendar', 'calendar'), ('Outlook', 'mail'), ('Razorpay', 'wallet'), ('Biometric machines', 'finger')]

# Who it is for (instead of customer logos until there are real ones)
FOR_WHOM = ['Creative agencies', 'IT and software teams', 'Marketing firms', 'Consultancies', 'Design studios', 'Growing multi-branch teams']

FAQS = [
    ('What is Assertive Hub?', 'An online HR and work-management system for small and growing companies in India: attendance, leave, tasks, time tracking, meetings, performance, payroll and more, in one place.'),
    ('How does the free trial work?', 'Sign up with your company name and choose your web address. You get a {trial}-day trial with every feature switched on. No card is needed. After the trial you pick a plan; until then your workspace becomes read-only, and nothing is deleted.'),
    ('What is my company\'s web address?', 'Every company gets its own address, like yourcompany.{domain}. Your team signs in there, and your data is kept separate from every other company.'),
    ('Is our data safe and private?', 'Each company has its own separate database. Passwords are stored hashed, the site uses HTTPS, and every change is recorded in an audit log. We never sell data or show ads.'),
    ('Can we use it on mobile phones?', 'Yes. It works in any modern browser on phones, tablets and computers, and can be added to the home screen like an app.'),
    ('Do you support several branches?', 'Yes. Each branch can have its own working hours, holidays, admins and reports. Higher plans include more branches.'),
    ('How do payments and GST invoices work?', 'You pay by UPI, card or net banking through Razorpay. GST at 18% is added, and a GST invoice is created for every payment. A yearly plan costs ten months.'),
    ('Can we send WhatsApp or SMS alerts?', 'Yes, on plans that include alerts. Your company connects its own WhatsApp Business (Meta) and SMS (DLT-registered) accounts, and employees receive messages only after they verify their number and opt in.'),
    ('Can we change or cancel our plan?', 'You can change plan from Settings, Plan and billing at any time. Plans do not renew automatically, so there is nothing to cancel; if you stop paying, your workspace stays readable so you can export your data.'),
    ('Do you help us get started?', 'Yes. Book a demo and we will walk you through setting up your company, branches and people.'),
]

# Real customer quotes only. Leave empty until you have permission to publish them.
# Example: {'quote': '...', 'name': 'Name', 'role': 'HR Manager', 'company': 'Company'}
TESTIMONIALS = []

TEAM_SIZES = ['1-15', '16-50', '51-150', '151-500', '500+']
CONTACT_TOPICS = ['Sales question', 'Support', 'Billing', 'Partnership', 'Something else']
