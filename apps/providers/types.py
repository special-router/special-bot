from dataclasses import dataclass


@dataclass(frozen=True)
class CanonicalEndpoint:
    external_id: str
    protocol: str
    transport: str
    security: str
    host: str
    port: int
    region: str = ''
    server_name: str = ''
    path: str = ''
    service_name: str = ''
    public_key: str = ''
    short_id: str = ''
    fingerprint: str = ''
    flow: str = ''
    host_header: str = ''
    transport_mode: str = ''
    alpn: str = ''
