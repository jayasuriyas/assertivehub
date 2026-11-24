ASSERTIVE HUB AS A SERVICE (INDIA)
==================================
Sell the hub to many companies. Each company gets its own address (acme.yourdomain.in) and its own
separate database file. Prices are in rupees, GST 18% is added, payments go through Razorpay.
This needs the Django version on a VPS or Python host (not Hostinger shared hosting).
Without HUB_SAAS=1 the app works exactly as before, for one company.

1. WHAT YOU NEED
- A domain, e.g. assertivehub.in, with a WILDCARD DNS record:  *.assertivehub.in  ->  your server IP
  (plus the plain assertivehub.in and www).
- A wildcard HTTPS certificate (*.assertivehub.in). Easiest: Caddy or certbot with your DNS provider's plugin.
- A Razorpay account (KYC done) -> Key ID, Key Secret. Create a webhook to
  https://assertivehub.in/razorpay/webhook for the event "payment.captured" and note its secret.
- A GST registration, and a CA to confirm the SAC code and invoice wording.

2. SETTINGS (environment variables)
HUB_SAAS=1
HUB_BASE_DOMAIN=assertivehub.in
DJANGO_HTTPS=1
DJANGO_TRUSTED_ORIGINS=https://assertivehub.in
HUB_OWNER_PASSWORD=<a long password for your owner panel>
RAZORPAY_KEY_ID=...   RAZORPAY_KEY_SECRET=...   RAZORPAY_WEBHOOK_SECRET=...
HUB_SELLER_NAME="Your registered business name"   HUB_SELLER_GSTIN=...   HUB_SELLER_ADDRESS="..."
HUB_SELLER_STATE="Tamil Nadu"      (decides CGST+SGST vs IGST)     HUB_SELLER_SAC=998431 (confirm with your CA)
HUB_EMAIL_HOST / _PORT / _USER / _PASS / _FROM   for welcome and reminder emails
HUB_DATA_DIR=/var/lib/assertive-hub     (keep this folder backed up: every company lives inside)
HUB_PRODUCT_NAME="Assertive Hub"

3. FIRST RUN
python manage.py migrate
gunicorn config.wsgi --bind 127.0.0.1:8000 --workers 3     (put Caddy/nginx in front, pass the Host header)
Daily reminders, add to cron:   0 9 * * *  cd /path && python manage.py saas_cron
Sales page: https://assertivehub.in   Owner panel: https://assertivehub.in/owner/

4. HOW IT WORKS
- Sign up: company, address, mobile (Indian), state, admin login -> 14-day trial with ALL features (Pro level).
- After the trial, or after a plan ends, the workspace becomes READ-ONLY (people can sign in and see data, nothing can be changed)
  until the super admin pays under Settings > Plan and billing. Nothing is deleted.
- Plans (edit saas/plans.py):  Starter Rs 999 / 15 people,  Business Rs 2,499 / 50 people (+ notices, documents, assets, onboarding),
  Pro Rs 4,999 / 150 people (+ payroll with PF/ESI, biometric machines, recruitment, invoices).
  Business also includes the shift rota and phone check-in (location radius, selfie, QR).  Yearly = 10 months price.
- Plan limits are enforced on the server: modules outside the plan are locked, and people beyond the limit cannot be added.
- Each payment makes a GST invoice (number like AI/2627/0001, financial year based), CGST+SGST for your own state, IGST for others.
  Renewal is manual (the customer pays again; a new payment adds a month/year on top of the remaining time).
  Cron emails remind at 3 days and 1 day before the end and after it ends.
- The webhook makes sure a payment is recorded even if the customer closes the browser after paying.
- Owner panel: all companies, state (trial/active/expired), approx monthly revenue, + trial days, change plan, record a manual/offline
  payment (adds days), suspend/resume, and "Open as admin" for support (a 5-minute one-time link, logged).
- Demo payments: set HUB_DEMO_PAY=1 WITHOUT Razorpay keys to try the flow without real money. Never use it in production.

4b. PUBLIC WEBSITE (assertivehub.in and www.assertivehub.in)
- Pages: Home, About, Products (+ one page per product area at /products/<area>), Features (plan-by-plan table), Pricing,
  Contact, Book a demo, FAQs, Blog, Log in (type your company name -> sent to yourcompany.assertivehub.in; "forgot address"
  e-mails the workspace address to the sign-up e-mail), Sign up, Terms, Privacy, plus /sitemap.xml and /robots.txt.
- Prices, plan names and which plan has which feature come from saas/plans.py, so the website never disagrees with billing.
- Words (product areas, FAQs, "built for" list, testimonials) are in saas/site_content.py. TESTIMONIALS is empty on purpose:
  add only real quotes you have permission to publish; the section appears once there is at least one.
- No JavaScript and a strict Content-Security-Policy on every public page.
- Contact and demo requests are saved and listed in Owner panel > Leads (mark contacted/closed, private notes).
  Set HUB_OWNER_EMAIL to get an e-mail for each new one. Spam protection: hidden honeypot field + 5 per hour per network.
- Blog: Owner panel > Blog. Write in plain text (blank line = paragraph, "## " heading, "- " bullet, **bold**, [text](https://...)).
  Three starter articles are included as DRAFTS - read, edit and tick Published.
- Optional settings shown on the Contact page: HUB_CONTACT_EMAIL, HUB_CONTACT_PHONE, HUB_SELLER_ADDRESS.
- Tests: HUB_SAAS=1 HUB_BASE_DOMAIN=example.test python manage.py test saas

5. BEFORE YOU SELL (not done by code)
- Replace the bracketed parts of /terms and /privacy with a lawyer's text (DPDP Act 2023: you are a processor for the employee data).
- Ask your CA about SAC, invoice format, and TDS/GST filing. Keep the Razorpay reports for GST returns.
- Back up HUB_DATA_DIR daily off the server (tenants/<name>/hub.sqlite3 per company, plus platform.sqlite3).
- SQLite per company is fine for hundreds of small companies. At several hundred active customers plan a move to PostgreSQL.
- Not included yet: automatic recurring billing (Razorpay Subscriptions), customer self-delete, custom domains, refunds screen (do refunds in Razorpay).
