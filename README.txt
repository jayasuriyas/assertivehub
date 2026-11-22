ASSERTIVE HUB - DJANGO VERSION
==============================
Same screens and features as the PHP version (roles, tasks, attendance, clients, payments, themes, logos,
footer, work time). The backend is Django with a real database (SQLite file: data/hub.sqlite3).

RUN ON YOUR COMPUTER (VS Code)
  1. Install Python 3.10 or newer (python.org). Check: python --version
  2. Open this folder in VS Code, open the terminal (Ctrl+`) and run:
       python -m venv .venv
       Windows:  .venv\Scripts\activate        Mac/Linux:  source .venv/bin/activate
       pip install -r requirements.txt
       python manage.py migrate
       python manage.py runserver
  3. Open http://127.0.0.1:8000 and create the super admin account.
  Start fresh: stop the server, delete the data folder, run "python manage.py migrate" again.

PUT IT ON A SERVER (Hostinger VPS, or any Python host)
  Shared/Business web hosting cannot run Django. Use a VPS (Hostinger has a Django template) or a Python host.
  On a VPS (Ubuntu), roughly:
    1. Upload this folder to /srv/assertive-hub, then:
         cd /srv/assertive-hub && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
         .venv/bin/python manage.py migrate
    2. Set environment variables for the service:
         DJANGO_ALLOWED_HOSTS=yourdomain.com
         DJANGO_TRUSTED_ORIGINS=https://yourdomain.com
         DJANGO_HTTPS=1                (once SSL is on)
         HUB_DATA_DIR=/srv/assertive-data   (optional: keep data outside the code folder)
    3. Run with:  .venv/bin/gunicorn config.wsgi:application --bind 127.0.0.1:8000 --workers 2
       (set it up as a systemd service so it restarts), and put Nginx in front with a free Let's Encrypt SSL.
  Keep DJANGO_DEBUG unset (off) on a real server.

Leave
  Employees and managers request leave under Leave: full day (one or more days), half day (first or second half),
  1 hour or 2 hours, with a reason. Managers approve their team's requests; admins and the super admin approve anyone's.
  Nobody can approve their own request (a manager's own leave goes to an admin). Approved full days are marked
  "On leave" in attendance automatically.

Menu, welcome card and weather
  The left menu can be minimised to icons only (button at the top of the menu) and the choice is remembered.
  The welcome card has a time-of-day greeting and gentle animations, and the quote changes every hour.
  The temperature uses the visitor's location (browser permission, or an approximate location from the network if
  they decline). The city in Settings is used only if the location cannot be found.

BACKUPS
  Every save writes a copy to data/backups: one per day (last 30 days) and one per month, kept forever.
  Settings > Download backup saves a copy to your own device; Restore (super admin) puts one back.
  Also copy data/hub.sqlite3 now and then. Never delete the data folder.

FORGOT A PASSWORD
  Super admin: Users > Edit > Generate sets a new password for anyone.
  Forgot the super admin password: python manage.py changepassword <username>

MOVING FROM THE PHP VERSION
  In the PHP panel use Settings > Download backup, then in this version sign in as super admin and use
  Settings > Restore from backup. Logins are not in the backup; create them again under Users.

WHAT'S NEW (v4): peer ratings, attendance lock + correction requests, pagination, birthday/anniversary highlight, district weather, corner menu toggle. See the PHP README notes for details; behaviour is identical.
Core team: tick "Core team member" on an employee to leave them out of attendance, performance and being rated; they still rate others.
Work report page (day/week/month totals per employee).
- Menu icon logo: Settings > Menu icon logo (light and dark). Used when the menu is minimised, on the phone top bar and as the browser tab icon. Also: Django server time now defaults to Asia/Kolkata (set HUB_TZ to change).

WHAT'S NEW (v5)
- Modules: Settings > Modules (super admin) switches each module on or off. Off hides it for everyone and keeps its data. New modules start switched off.
- New modules: Notice board, Documents (files up to 700 KB), Assets, Payroll and payslips (loss of pay from absent days, print view, employees see only paid payslips), Onboarding and exit checklists, Recruitment, Invoices (print view), Audit log (who changed what and when).
- Leave: set a yearly allowance in Settings > Modules > Options. Balance shows on the Leave page; requests over the balance are blocked and approvers get a warning. (Checked in the screens, not on the server.)
- Notifications: bell button shows what needs you. Optional e-mail alerts for leave and attendance requests (Settings > Modules > Options; needs staff e-mail addresses and working mail on your host).
- Forgot password: login page asks the admins to reset it; they see the request on the Users page.
- Anonymous ratings option hides the rater's name from managers (admins still see it).
- CSV downloads on the Work report page.
- Salary is visible to admins and to the employee only. A backup reminder shows to the super admin after 30 days.
Recovery if the super admin password is lost (Django): run  python3 manage.py changepassword <username>


SHIFTS, PHONE CHECK-IN, BIOMETRIC MACHINES, PF / ESI  (Django version only)
===========================================================================
Switch each on under Settings > Modules, then set it up in Settings.
1. Shift rota: define shifts (name, start, end, grace). Give each person a default shift on their profile, and use the
   Shift rota page for a weekly plan and day-offs. Late is worked out from the person's shift, not the company hours.
2. Phone check-in: staff press Check in / Check out on their Home page. You choose the rules:
   - Location radius: add one or more offices (latitude, longitude, radius in metres). A check-in outside every radius is refused.
     A weak GPS signal (worse than 150 m) is also refused. Phones need HTTPS for location (a SaaS or real domain is fine).
   - Selfie: a small picture is saved with the punch; managers and admins can open it from the attendance page.
   - QR: show today's QR code at the entrance (Settings > Check-in). Staff scan it to unlock check-in.
   Check-ins are locked like any attendance the person made themselves; admins can still correct them.
3. Biometric machines (fingerprint or face): Settings > Biometric devices shows your device key and address.
   - ZKTeco, eSSL, Realtime and similar machines with "Cloud server / ADMS": set the server address to your company address,
     add the machine's serial number in Settings. Punches arrive by themselves within a minute or two.
   - Any other machine or software: send  POST <address>/device/punch  with header X-Device-Key and
     {"punches":[{"pin":"12","time":"2026-11-03 09:01:00"}]}.
   - A punch is matched to a person by the Biometric ID on the profile (or the employee code if that is empty).
   - Double taps within 2 minutes are ignored. Days already marked by a manager or admin are not overwritten.
   Not tested with a real machine: machines differ by firmware, so test one before you promise it to a customer.
4. Payroll rules (Settings > Payroll rules): PF 12% of basic (capped at Rs 15,000 wage), ESI 0.75% employee / 3.25% employer
   when the salary is Rs 21,000 or less, and professional tax slabs you enter for your state. The payslip shows the deductions,
   and "PF / ESI statement" downloads the monthly figures. TDS (income tax) is NOT calculated.
In the SaaS version these are plan features: shift rota and check-in from Business, biometric and payroll from Pro.


PHONE APP (both versions)
=========================
On a phone the panel looks and feels like an app: a compact top bar, an icon-only bottom bar (Home, People, Attendance,
Tasks, Clients, More), slide-up sheets and smooth screen changes.
To install it: open the address in Chrome (Android) and tap Install when the banner appears, or in Safari (iPhone) tap Share,
then Add to Home Screen. It then opens full screen with its own icon. The app needs internet for data (it only keeps the
screen itself for a fast start).
For Check-in with location the address must be HTTPS.


NEW LOOK (November 2026)
========================
- Sign-in page: centred card with "Remember me" and "Forgot password?". The super admin can set a background picture
  under Settings > Sign-in page (paste an https:// image link). Without one, a soft default background is used.
- Light and dark only (the colour choices were removed). Each person picks in Settings or with the moon/sun button
  next to their name.
- The product icon and the company name show at the top left. Company logo and menu icon uploads were removed.
- Sidebar: search (press / to jump to it, finds pages, people and clients), Workspace / Insights / Manage sections,
  "Your day" progress, and your name with light/dark and sign-out buttons.
- Dashboard: greeting with today's summary, period and department filters, Export (attendance CSV), "Needs you" queue,
  today's attendance, attendance gauge, attendance trend, work completed and on-time arrivals charts.


POLISH AND SECURITY (November 2026)
===================================
- Icons replace every emoji. Glass (frosted) finish on all cards, bars and pop-ups, in light and dark.
- Number cards have icons and colours; on Attendance they also show each status as a share of the team.
- Dashboard: Top performers podium with score rings, Team pulse (attendance by department), Out today with leave details.
- Motion: the bell rings while something waits, menu counts pulse, "Needs you" glows when a request is pending,
  a pop-up appears when something new arrives, and approving, rejecting or sending a request plays a short animation.
  People who turn on "reduce motion" on their device get no animation.
- Security: Content-Security-Policy, Permissions-Policy (location and camera only for this site), Referrer-Policy,
  Cross-Origin-Opener-Policy, HSTS over https, no caching of API answers, CSV downloads protected against spreadsheet
  formula injection, and the Hostinger .htaccess now blocks data, backup, log and hidden files.
- Settings page: sections are grouped (Personal, Company, People and access, Work, System) as accordions. Each shows an
  icon, a one-line description and its current value (for example "09:00 – 18:00 · Mon–Fri"). There is a search box,
  Expand all / Collapse all, and a section index on the left. Open sections are remembered on each device.


SECURITY REVIEW FIXES (7 November 2026)
=======================================
An independent review found these, all fixed and tested:
- SaaS: a selfie file name taken from a restored backup could read any file on the server (other companies' data,
  secret key). Selfie names are now strictly checked and must stay inside the selfies folder; restore drops them.
- Stored script injection: record ids, client/employee reference fields and number fields are now validated on the server
  (both versions); pictures must be real images and documents real files; remaining unescaped fields escaped.
  The page now only runs its own script (Content-Security-Policy with the script's hash), so even injected HTML cannot run.
  IMPORTANT for Hostinger: .htaccess contains a fingerprint (sha256) of index.html's script. If index.html is edited
  by hand the page will not load until that fingerprint is updated, so always upload index.html and .htaccess together
  from the same release. (Django works this out by itself.)
- Login lockout: Django was not keeping failed attempts (rolled back) - fixed. Both versions now lock by
  username + network address (5 tries) and by address (30 tries) for 15 minutes, so a stranger cannot lock out your
  super admin from everywhere, and junk usernames can no longer bloat the login file.
- Behind a reverse proxy set HUB_TRUST_PROXY=1 so the real visitor address is used (Django).
- SaaS billing: changing plan while time is left now converts the remaining days by value instead of stacking them.
- SaaS restore now respects the plan's people limit and modules.
- Audit and e-mail queues are private to each request (no mixing between companies).
- The one-time sign-in link (after signup / owner support) now works only once.
- Owner panel: 5 wrong passwords per address (25 overall) locks it for 30 minutes.
- Signup rate limit can no longer be dodged with a fake X-Forwarded-For header.
- Anonymous ratings: the record id no longer reveals who rated.
- PHP: changing your password signs out your other sessions (Django already did).
- Biometric device serial numbers are no longer sent to managers and employees.
Still recommended before selling: e-mail verification or a captcha on signup, two-step login for super admins,
HTTPS everywhere, and daily off-server backups.
