"""Public website, leads and blog. Run in SaaS mode:
HUB_SAAS=1 HUB_BASE_DOMAIN=example.test HUB_DATA_DIR=/tmp/hubsaas python manage.py test saas"""
import datetime
from unittest import mock

from django.conf import settings
from django.core import signing
from django.test import TestCase, override_settings

from . import plans, site
from .models import Lead, Post, Tenant
from .owner import COOKIE

H = settings.BASE_DOMAIN
PAGES = ['/', '/about', '/products', '/features', '/pricing', '/faq', '/contact', '/demo', '/blog', '/login', '/signup', '/terms', '/privacy'] + \
        ['/products/%s' % a['key'] for a in site.C.AREAS]


class Website(TestCase):
    def get(self, url, **kw):
        return self.client.get(url, HTTP_HOST=H, **kw)

    def post(self, url, data):
        return self.client.post(url, data, HTTP_HOST=H)

    def test_every_page_renders_without_scripts(self):
        for u in PAGES:
            r = self.get(u)
            self.assertEqual(r.status_code, 200, u)
            h = r.content.decode()
            self.assertNotIn('<script', h, u)
            self.assertIn('<title>', h)
            self.assertIn("default-src 'none'", r['Content-Security-Policy'])
            self.assertNotIn('script-src', r['Content-Security-Policy'])
            self.assertIn('href="/pricing"', h)  # shared header/footer

    def test_www_is_the_same_site(self):
        self.assertEqual(self.client.get('/pricing', HTTP_HOST='www.' + H).status_code, 200)

    def test_pricing_comes_from_plans(self):
        h = self.get('/pricing').content.decode()
        for p in plans.PLANS.values():
            self.assertIn('₹%s' % format(p['month'], ','), h)
            self.assertIn('₹%s' % format(p['month'] * plans.YEAR_MONTHS_PAID, ','), h)

    def test_unknown_product_is_404_page(self):
        r = self.get('/products/nothing')
        self.assertEqual(r.status_code, 404)
        self.assertIn('could not find that page', r.content.decode())

    def test_css_and_favicon(self):
        self.assertEqual(self.get('/site.css')['Content-Type'], 'text/css; charset=utf-8')
        self.assertEqual(self.get('/logo.png')['Content-Type'], 'image/png')

    def test_contact_creates_lead(self):
        with mock.patch('saas.site.mail.send') as m, mock.patch.dict('os.environ', {'HUB_OWNER_EMAIL': 'owner@example.com'}):
            r = self.post('/contact', {'name': 'Ravi', 'email': 'ravi@example.com', 'message': 'Do you support 3 branches?', 'topic': 'Sales question'})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r['Location'].endswith('/contact?sent=1'))
        l = Lead.objects.get()
        self.assertEqual((l.kind, l.topic, l.status), ('contact', 'Sales question', 'new'))
        m.assert_called_once()
        self.assertIn('Thank you', self.get('/contact?sent=1').content.decode())

    def test_contact_validation_and_honeypot(self):
        r = self.post('/contact', {'name': 'Ravi', 'email': 'bad', 'message': 'Hello there friend'})
        self.assertEqual(r.status_code, 400)
        self.assertIn('valid email', r.content.decode())
        self.assertIn('value="Ravi"', r.content.decode())  # keeps what was typed
        self.post('/contact', {'name': 'Bot', 'email': 'b@example.com', 'message': 'Buy now buy now', 'website': 'spam.example'})
        self.assertEqual(Lead.objects.count(), 0)

    def test_demo_needs_company_and_team_size(self):
        self.assertEqual(self.post('/demo', {'name': 'Asha', 'email': 'a@example.com', 'company': 'Acme'}).status_code, 400)
        r = self.post('/demo', {'name': 'Asha', 'email': 'a@example.com', 'company': 'Acme', 'team_size': '16-50', 'preferred': 'Tue pm'})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Lead.objects.get().kind, 'demo')

    def test_form_rate_limit(self):
        for i in range(5):
            self.post('/contact', {'name': 'Ravi', 'email': 'r%d@example.com' % i, 'message': 'Message number %d here' % i})
        r = self.post('/contact', {'name': 'Ravi', 'email': 'r9@example.com', 'message': 'One message too many'})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(Lead.objects.count(), 5)

    def test_login_redirects_to_company_subdomain(self):
        Tenant.objects.create(slug='acme', name='Acme', owner_name='A', owner_email='Boss@Acme.example', trial_ends=datetime.date.today())
        r = self.get('/login', data={'company': 'Acme'})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r['Location'], 'http://acme.%s/' % H)
        self.assertEqual(self.get('/login', data={'company': 'https://acme.%s/x' % H})['Location'], 'http://acme.%s/' % H)
        r = self.get('/login', data={'company': 'nobody'})
        self.assertEqual(r.status_code, 404)
        self.assertIn('could not find a workspace', r.content.decode())
        self.assertEqual(self.get('/login', data={'company': 'www'}).status_code, 404)  # reserved name is never a company

    def test_forgot_address_same_answer_either_way(self):
        Tenant.objects.create(slug='acme', name='Acme', owner_name='A', owner_email='boss@acme.example', trial_ends=datetime.date.today())
        with mock.patch('saas.site.mail.send') as m:
            a = self.post('/login/find', {'email': 'BOSS@acme.example'})
            b = self.post('/login/find', {'email': 'nobody@acme.example'})
        self.assertEqual(a['Location'], b['Location'])
        m.assert_called_once()
        self.assertIn('acme.%s' % H, m.call_args[0][2])

    def test_blog_drafts_hidden_and_body_escaped(self):
        self.assertEqual(Post.objects.filter(published=True).count(), 0)  # starter posts are drafts
        d = Post.objects.first()
        self.assertEqual(self.get('/blog/%s' % d.slug).status_code, 404)
        p = Post.objects.create(slug='hello', title='Hello', body='## Head\n\nText with **bold** and <script>alert(1)</script>\n\n- one\n- two\n\n[x](javascript:alert(1)) [ok](https://example.com)',
                                published=True, published_at=datetime.datetime.now(datetime.timezone.utc))
        h = self.get('/blog/hello').content.decode()
        self.assertIn('<h2>Head</h2>', h)
        self.assertIn('<b>bold</b>', h)
        self.assertIn('&lt;script&gt;', h)
        self.assertNotIn('href="javascript', h)
        self.assertIn('href="https://example.com"', h)
        self.assertIn('<ul><li>one</li><li>two</li></ul>', h)
        self.assertIn('/blog/hello', self.get('/blog').content.decode())
        self.assertIn('/blog/hello', self.get('/').content.decode())
        sm = self.get('/sitemap.xml').content.decode()
        self.assertIn('/blog/hello', sm)
        self.assertNotIn('/blog/%s' % d.slug, sm)
        self.assertIn('/products/meetings', sm)
        p.delete()

    def test_robots(self):
        t = self.get('/robots.txt').content.decode()
        self.assertIn('Disallow: /owner/', t)
        self.assertIn('Sitemap: http://%s/sitemap.xml' % H, t)

    def test_signup_prefills_company(self):
        self.assertIn('value="Acme Studio"', self.get('/signup', data={'company': 'Acme Studio'}).content.decode())

    def test_no_made_up_testimonials(self):
        self.assertEqual(site.C.TESTIMONIALS, [])
        self.assertNotIn('What our customers say', self.get('/').content.decode())


class OwnerTabs(TestCase):
    def setUp(self):
        self.client.cookies[COOKIE] = signing.dumps('owner', salt='hub-owner')

    def test_owner_pages_need_login(self):
        self.client.cookies.pop(COOKIE)
        for u in ('/owner/leads', '/owner/blog', '/owner/blog/new'):
            self.assertEqual(self.client.get(u, HTTP_HOST=H)['Location'], '/owner/login')
        self.assertEqual(self.client.post('/owner/leads/act', {'id': 1, 'act': 'closed'}, HTTP_HOST=H).status_code, 403)

    def test_leads_list_and_status(self):
        l = Lead.objects.create(kind='demo', name='Asha <b>', email='a@example.com', company='Acme', team_size='16-50', message='Show me')
        h = self.client.get('/owner/leads', HTTP_HOST=H).content.decode()
        self.assertIn('Asha &lt;b&gt;', h)
        self.assertIn('Leads (1 new)', h)
        self.client.post('/owner/leads/act', {'id': l.pk, 'act': 'contacted'}, HTTP_HOST=H)
        self.client.post('/owner/leads/act', {'id': l.pk, 'act': 'note', 'note': 'Call Friday'}, HTTP_HOST=H)
        l.refresh_from_db()
        self.assertEqual((l.status, l.note), ('contacted', 'Call Friday'))

    def test_write_and_publish_post(self):
        r = self.client.post('/owner/blog/new', {'title': 'Our new release', 'body': 'Lots of new things to talk about.', 'tag': 'News', 'published': '1'}, HTTP_HOST=H)
        p = Post.objects.get(slug='our-new-release')
        self.assertTrue(p.published and p.published_at)
        self.assertEqual(r['Location'], '/owner/blog/%d?saved=1' % p.pk)
        self.assertEqual(self.client.get('/blog/our-new-release', HTTP_HOST=H).status_code, 200)
        r = self.client.post('/owner/blog/new', {'title': 'Our new release', 'body': 'Another body text here.'}, HTTP_HOST=H)
        self.assertEqual(r.status_code, 400)  # same slug
        self.client.post('/owner/blog/%d' % p.pk, {'act': 'delete'}, HTTP_HOST=H)
        self.assertFalse(Post.objects.filter(pk=p.pk).exists())

    def test_companies_tab_still_works(self):
        r = self.client.get('/owner/', HTTP_HOST=H)
        self.assertEqual(r.status_code, 200)
        self.assertIn('No companies yet', r.content.decode())
