from django.urls import path

from . import billing, pviews, owner

urlpatterns = [
    path('', pviews.home),
    path('signup', pviews.signup),
    path('check', pviews.check),
    path('terms', pviews.terms),
    path('privacy', pviews.privacy),
    path('razorpay/webhook', billing.webhook),
    path('owner/', owner.panel),
    path('owner/login', owner.login),
    path('owner/logout', owner.logout),
    path('owner/act', owner.act),
    path('owner/go/<str:slug>', owner.go),
]
