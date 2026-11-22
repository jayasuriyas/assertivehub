from django.urls import path
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
]
