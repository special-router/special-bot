from telegram.ext import ContextTypes

ROUTER_STATE_SERIAL = "serial"
ROUTER_STATE_SHIPPING = "shipping"

def set_router_state(context: ContextTypes.DEFAULT_TYPE, state: str | None) -> None:
    context.user_data['router_state'] = state


def get_router_state(context: ContextTypes.DEFAULT_TYPE) -> str | None:
    return context.user_data.get('router_state')
