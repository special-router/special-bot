from django.urls import path

from api.v1.payments.cryptobot.views import cryptobot_webhook


urlpatterns = [
    path('cryptobot/webhook/', cryptobot_webhook, name='cryptobot-webhook'),
]
