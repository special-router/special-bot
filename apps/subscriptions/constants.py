from typing import Final

# Тарифы продления подписки Special Router (из flow)
ROUTER_TARIFFS_RUB: Final[dict[int, int]] = {
    1: 500,
    3: 1400,
    6: 2700,
    12: 5000,
}

ROUTER_TARIFFS_USDT: Final[dict[int, float]] = {
    1: 5,
    3: 14,
    6: 27,
    12: 53,
}

ROUTER_PURCHASE_PRICE_RUB: Final[int] = 12000
ROUTER_PURCHASE_PRICE_USDT: Final[float] = 120
ROUTER_ACTIVATION_FIRST_MONTH_RUB: Final[int] = 500

SUPPORT_URL: Final[str] = 'https://t.me/Special_Wifi_Official'
SUPPORT_USERNAME: Final[str] = '@Special_Wifi_Official'
