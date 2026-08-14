from django.urls import include, path

from api.views.cryptobot_webhook import cryptobot_webhook


urlpatterns = [
    path('v1/', include('api.v1.urls')),
    # Mounted under /api/ in bot/urls.py; CRYPTOBOT_WEBHOOK_PATH default is
    # /api/webhook/cryptobot/ — the segment below that prefix is registered here.
    path('webhook/cryptobot/', cryptobot_webhook, name='cryptobot-webhook'),
]
