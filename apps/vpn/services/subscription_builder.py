import base64

from django.conf import settings

from apps.vpn.models import UserVPN

BALANCER_GROUP_NAME = 'Special VPN'


def _yaml_quote(value: str) -> str:
    if any(c in value for c in ':{}[],&*#?|-<>=!%@\\"'):
        escaped = value.replace('"', '\\"')
        return f'"{escaped}"'
    return value


def _entry_to_clash_proxy_lines(entry: dict, indent: str = '  ') -> list[str]:
    name = entry['name']
    return [
        f'{indent}- name: {_yaml_quote(name)}',
        f'{indent}  type: vless',
        f'{indent}  server: {_yaml_quote(entry["server"])}',
        f'{indent}  port: {entry["port"]}',
        f'{indent}  uuid: {entry["uuid"]}',
        f'{indent}  network: {entry.get("network", "tcp")}',
        f'{indent}  tls: true',
        f'{indent}  udp: true',
        f'{indent}  flow: xtls-rprx-vision',
        f'{indent}  servername: {_yaml_quote(entry["sni"])}',
        f'{indent}  client-fingerprint: {entry.get("fp", "chrome")}',
        f'{indent}  reality-opts:',
        f'{indent}    public-key: {_yaml_quote(entry["pbk"])}',
        f'{indent}    short-id: {_yaml_quote(entry["sid"])}',
    ]


def build_clash_subscription(user_vpn: UserVPN) -> str:
    if not user_vpn.vless_links:
        raise ValueError('VLESS links are not configured for this key')

    proxy_names = [entry['name'] for entry in user_vpn.vless_links]
    lines = ['proxies:']
    for entry in user_vpn.vless_links:
        lines.extend(_entry_to_clash_proxy_lines(entry))

    lines.extend(
        [
            'proxy-groups:',
            f'  - name: {_yaml_quote(BALANCER_GROUP_NAME)}',
            '    type: load-balance',
            '    url: http://www.gstatic.com/generate_204',
            '    interval: 300',
            '    proxies:',
        ]
    )
    for name in proxy_names:
        lines.append(f'      - {_yaml_quote(name)}')

    return '\n'.join(lines) + '\n'


def build_subscription_payload(user_vpn: UserVPN) -> str:
    yaml_content = build_clash_subscription(user_vpn)
    return base64.b64encode(yaml_content.encode()).decode()


def build_subscription_url(user_vpn: UserVPN) -> str:
    base_url = settings.SUBSCRIPTION_BASE_URL.rstrip('/')
    return f'{base_url}/api/v1/vpn/sub/{user_vpn.vpn_uuid}/'
