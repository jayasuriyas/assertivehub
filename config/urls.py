from django.urls import path
from core.v6 import notify as v6_notify, calsync as v6_calsync, files as v6_files, meetings as v6_meetings
from core import views, punch

urlpatterns = [
    path('', views.index),
    path('api/', views.api),
    path('manifest.webmanifest', views.manifest),
    path('sw.js', views.asset, {'name': 'sw.js'}),
    path('icon-192.png', views.asset, {'name': 'icon-192.png'}),
    path('icon-512.png', views.asset, {'name': 'icon-512.png'}),
    path('icon-maskable-512.png', views.asset, {'name': 'icon-maskable-512.png'}),
    path('icon-180.png', views.asset, {'name': 'icon-180.png'}),
    path('iclock/cdata', punch.adms_cdata),
    path('iclock/getrequest', punch.adms_getrequest),
    path('device/punch', punch.device_punch),
    # v6: provider webhooks, calendar OAuth return, attachment downloads, meeting RSVP links
    path('hooks/whatsapp/<str:token>', v6_notify.webhook_whatsapp),
    path('hooks/sms/<str:token>', v6_notify.webhook_sms),
    path('oauth/<str:provider>/callback', v6_calsync.oauth_callback),
    path('files/<int:aid>', v6_files.download),
    path('m/<str:token>', v6_meetings.rsvp_page),
]
