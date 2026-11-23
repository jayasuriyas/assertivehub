"""Plans, prices and GST. Edit this file to change what you sell. Prices are in rupees, before GST."""
import os

PRODUCT = os.environ.get('HUB_PRODUCT_NAME', 'Assertive Hub')
GST_RATE = 18            # percent, added on top of the price
TRIAL_DAYS = 14
YEAR_MONTHS_PAID = 10    # a yearly plan costs 10 months (2 months free)

CORE = ['attendance', 'leave', 'tasks', 'clients', 'calendar', 'performance', 'review', 'workrep', 'ratings', 'meetings', 'timetrack']
EXTRA1 = ['notices', 'documents', 'assets', 'checklists', 'rota', 'checkin', 'notify', 'calsync', 'branches']
EXTRA2 = ['payroll', 'recruitment', 'invoices', 'biometric']

PLANS = {
    'starter':  {'name': 'Starter',  'month': 999,  'emp': 15, 'branches': 1,  'modules': CORE,
                 'blurb': 'Attendance, leave, tasks, clients, ratings and reports'},
    'business': {'name': 'Business', 'month': 2499, 'emp': 50,  'branches': 3, 'modules': CORE + EXTRA1,
                 'blurb': 'Everything in Starter plus notice board, documents, assets, onboarding, shift rota and GPS/selfie/QR check-in'},
    'pro':      {'name': 'Pro',      'month': 4999, 'emp': 150, 'branches': None, 'modules': CORE + EXTRA1 + EXTRA2,
                 'blurb': 'Everything, including payroll (PF, ESI), biometric device attendance, recruitment and invoices'},
}
TRIAL_PLAN = 'pro'       # during the free trial people can try everything

# which collection needs which module
COLL_MODULE = {'notices': 'notices', 'documents': 'documents', 'assets': 'assets', 'candidates': 'recruitment',
               'invoices': 'invoices', 'payslips': 'payroll', 'checklists': 'checklists', 'rota': 'rota'}

STATES = ['Andhra Pradesh', 'Arunachal Pradesh', 'Assam', 'Bihar', 'Chhattisgarh', 'Goa', 'Gujarat', 'Haryana', 'Himachal Pradesh',
          'Jharkhand', 'Karnataka', 'Kerala', 'Madhya Pradesh', 'Maharashtra', 'Manipur', 'Meghalaya', 'Mizoram', 'Nagaland', 'Odisha',
          'Punjab', 'Rajasthan', 'Sikkim', 'Tamil Nadu', 'Telangana', 'Tripura', 'Uttar Pradesh', 'Uttarakhand', 'West Bengal',
          'Andaman and Nicobar Islands', 'Chandigarh', 'Dadra and Nagar Haveli and Daman and Diu', 'Delhi', 'Jammu and Kashmir',
          'Ladakh', 'Lakshadweep', 'Puducherry']


def price(plan, period):
    """(base, gst_total, total) in paise for a plan and 'month' or 'year'."""
    base = PLANS[plan]['month'] * (YEAR_MONTHS_PAID if period == 'year' else 1) * 100
    gst = round(base * GST_RATE / 100)
    return base, gst, base + gst


def gst_split(base, buyer_state):
    """Same state: CGST + SGST. Different state: IGST. Returns (cgst, sgst, igst) in paise."""
    seller = os.environ.get('HUB_SELLER_STATE', '').strip().lower()
    gst = round(base * GST_RATE / 100)
    if seller and buyer_state and seller == buyer_state.strip().lower():
        half = gst // 2
        return half, gst - half, 0
    return 0, 0, gst
