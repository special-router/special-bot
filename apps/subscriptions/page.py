"""Страница подписки, которую человек открывает в браузере.

Тот же ``/sub/<sub_id>`` отвечает приложению документом, а браузеру — страницей.
Разделение по ``Accept``, а не по одному User-Agent: приложение никогда не просит
``text/html``, и это единственный признак, который нельзя подделать случайно.

Готовый ``remnawave/subscription-page`` сюда не подходит по двум причинам, и обе
видны в его исходниках. Он тянет подписку из панели
(``backend/src/common/axios/axios.service.ts``: ``api/sub/<shortUuid>``), а
панель знает только наши собственные точки — девять стран зеркала в её ответе
отсутствуют. И числа он показывает панельные, а у нас срок в панели намеренно
далёкий: право доступа считает баланс, и второй срок рядом с ним рано или поздно
режет доступ оплатившему.

Схемы кнопок взяты из их же ``frontend/public/assets/app-config.json`` — это
единственное место, где они записаны авторитетно.

Страница содержит саму ссылку подписки, то есть данные доступа. Поэтому ответ
идёт с ``no-store`` (как и все ответы этого эндпоинта), ссылка не попадает ни в
лог, ни в заголовок, а неизвестный ``sub_id`` получает тот же ``404``, что и
раньше.
"""

import base64
from html import escape
from urllib.parse import quote, unquote

from django.conf import settings


# Приложение просит документ, браузер — страницу. Happ и v2rayNG умеют слать
# ``Accept: */*``, поэтому решает наличие ``text/html``, а не его отсутствие.
_HTML_ACCEPT = 'text/html'


def wants_page(request) -> bool:
    """Просит ли этот запрос страницу, а не документ подписки."""
    return _HTML_ACCEPT in request.META.get('HTTP_ACCEPT', '').casefold()


# Из app-config.json проекта remnawave/subscription-page. Подстановка у всех
# одинаковая — percent-encoded URL в конец, — кроме Shadowrocket, который ждёт
# base64. Порядок внутри платформы: сначала то, что там помечено isFeatured.
_APPS: tuple[tuple[str, str, str, str], ...] = (
    ('Happ', 'happ://add/', 'raw', 'ios android windows macos'),
    ('v2rayNG', 'v2rayng://install-config?name=SPECIAL&url=', 'quoted', 'android'),
    ('Streisand', 'streisand://import/', 'raw', 'ios'),
    ('Shadowrocket', 'sub://', 'base64', 'ios'),
    ('Clash Meta', 'clashmeta://install-config?name=SPECIAL&url=', 'quoted', 'android'),
    ('Clash Verge', 'clash://install-config?url=', 'quoted', 'windows macos linux'),
    ('FlClashX', 'flclashx://install-config?url=', 'quoted', 'android windows macos linux'),
    ('Stash', 'stash://install-config?url=', 'quoted', 'ios'),
)


def app_links(subscription_url: str) -> list[tuple[str, str, str]]:
    """Кнопки «добавить подписку» — название, ссылка, платформы."""
    result = []
    for name, scheme, encoding, platforms in _APPS:
        if encoding == 'quoted':
            target = scheme + quote(subscription_url, safe='')
        elif encoding == 'base64':
            target = scheme + base64.b64encode(subscription_url.encode()).decode()
        else:
            target = scheme + subscription_url
        result.append((name, target, platforms))
    return result


def endpoint_labels(links: list[str]) -> list[str]:
    """Названия точек ровно в том виде, в каком их увидит приложение.

    Читается тот же список, который уходит клиенту, поэтому страница не может
    разойтись с подпиской: разойтись было бы нечему.
    """
    labels = []
    for link in links:
        label = unquote(link.partition('#')[2]).strip()
        # Первая строка — не точка, а способ показать срок тому клиенту,
        # который не читает заголовков. На странице срок написан словами.
        if label and not label.startswith('📊'):
            labels.append(label)
    return labels


def _device_line(device) -> str:
    name = device.device_model or device.device_os or 'устройство'
    return escape(str(name)[:64])


def render(*, subscription_url: str, days: int, status_label: str,
           links: list[str], devices: list, device_limit: int) -> str:
    """Собрать страницу. Ничего не читает из сети и ничего не пишет."""
    title = escape(str(getattr(settings, 'SUBSCRIPTION_PROFILE_TITLE', 'VPN'))[:64])
    support = str(getattr(settings, 'SUBSCRIPTION_SUPPORT_URL', ''))[:200]
    announce = escape(str(getattr(settings, 'SUBSCRIPTION_ANNOUNCE_TEXT', ''))[:512])

    buttons = '\n'.join(
        f'<a class="app" href="{escape(target, quote=True)}">'
        f'<b>{escape(name)}</b><span>{escape(platforms)}</span></a>'
        for name, target, platforms in app_links(subscription_url)
    )
    countries = '\n'.join(f'<li>{escape(label)}</li>' for label in endpoint_labels(links))
    device_html = '\n'.join(f'<li>{_device_line(device)}</li>' for device in devices)
    if not device_html:
        device_html = '<li class="muted">пока ни одного</li>'

    announce_html = f'<p class="announce">{announce}</p>' if announce else ''
    support_html = (f'<a class="support" href="{escape(support, quote=True)}">Поддержка</a>'
                    if support else '')
    term = f'осталось {days} дн.' if days > 0 else escape(status_label)

    return _TEMPLATE.format(
        title=title, term=term, announce=announce_html,
        url=escape(subscription_url), url_attr=escape(subscription_url, quote=True),
        buttons=buttons, countries=countries, devices=device_html,
        device_used=len(devices), device_limit=device_limit, support=support_html,
    )


_TEMPLATE = """<!doctype html>
<html lang="ru"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<meta name="referrer" content="no-referrer">
<title>{title}</title>
<style>
:root{{color-scheme:dark}}
*{{box-sizing:border-box}}
body{{margin:0;padding:24px 16px 48px;background:#0f1115;color:#e8eaed;
font:16px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}}
main{{max-width:640px;margin:0 auto}}
h1{{font-size:22px;margin:0 0 4px}}
.term{{color:#9aa0a6;margin:0 0 20px}}
.announce{{background:#1c2333;border-left:3px solid #4c8dff;padding:10px 12px;
border-radius:6px;margin:0 0 20px}}
h2{{font-size:14px;text-transform:uppercase;letter-spacing:.08em;color:#9aa0a6;
margin:28px 0 10px}}
.url{{display:flex;gap:8px}}
.url input{{flex:1;min-width:0;background:#171a21;border:1px solid #2a2f3a;
color:#e8eaed;border-radius:8px;padding:11px 12px;font-size:13px}}
button{{background:#4c8dff;color:#0f1115;border:0;border-radius:8px;
padding:11px 16px;font-weight:600;cursor:pointer}}
button:active{{transform:translateY(1px)}}
.apps{{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px}}
.app{{display:block;background:#171a21;border:1px solid #2a2f3a;border-radius:8px;
padding:12px;text-decoration:none;color:#e8eaed}}
.app b{{display:block;font-size:15px}}
.app span{{font-size:11px;color:#9aa0a6}}
ul{{margin:0;padding-left:20px}}
li{{margin:3px 0}}
.muted{{color:#9aa0a6}}
.support{{display:inline-block;margin-top:24px;color:#4c8dff}}
</style></head><body><main>
<h1>{title}</h1>
<p class="term">{term}</p>
{announce}
<h2>Ссылка подписки</h2>
<div class="url">
  <input id="u" value="{url_attr}" readonly onclick="this.select()">
  <button onclick="navigator.clipboard.writeText(document.getElementById('u').value);
this.textContent='Скопировано'">Копировать</button>
</div>
<h2>Добавить в приложение</h2>
<div class="apps">{buttons}</div>
<h2>Страны</h2>
<ul>{countries}</ul>
<h2>Устройства ({device_used} из {device_limit})</h2>
<ul>{devices}</ul>
{support}
</main></body></html>
"""
