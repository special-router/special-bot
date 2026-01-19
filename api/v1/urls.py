from django.urls import include, path


urlpatterns = [
    path('vpn/', include('api.v1.vpn.urls')),
]
