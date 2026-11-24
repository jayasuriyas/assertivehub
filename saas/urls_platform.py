from django.urls import path

from . import billing, pviews, owner, site
from core.v6 import calsync as v6_calsync

urlpatterns = [
    # public website
    path('', site.home),
    path('about', site.about),
    path('products', site.products),
    path('products/<slug:key>', site.product),
    path('features', site.features),
    path('pricing', site.pricing),
    path('faq', site.faq),
    path('contact', site.contact),
    path('demo', site.demo),
    path('blog', site.blog),
    path('blog/<slug:slug>', site.blog_post),
    path('login', site.login),
    path('login/find', site.login_find),
    path('sitemap.xml', site.sitemap),
    path('robots.txt', site.robots),
    path('site.css', site.css),
    path('favicon.svg', site.favicon),
    path('signup', pviews.signup),
    path('check', pviews.check),
    path('terms', pviews.terms),
    path('privacy', pviews.privacy),
    path('razorpay/webhook', billing.webhook),
    path('owner/', owner.panel),
    path('owner/login', owner.login),
    path('owner/logout', owner.logout),
    path('owner/act', owner.act),
    path('owner/leads', owner.leads),
    path('owner/leads/act', owner.lead_act),
    path('owner/blog', owner.blog),
    path('owner/blog/<str:pid>', owner.blog_edit),
    path('owner/go/<str:slug>', owner.go),
    path('oauth/<str:provider>/callback', v6_calsync.oauth_callback),  # v6: one callback for every company (no wildcard redirect URIs)
]

handler404 = site.not_found
