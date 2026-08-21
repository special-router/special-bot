"""Составной документ для Happ: массив профилей, внутри каждого — лестница."""
from django.test import SimpleTestCase, override_settings

from apps.subscriptions.views import (
    _build_xray_json,
    _cascade,
    _mirror_xray_profiles,
    _wants_hysteria_outbound,
    _xray_outbound_from_link,
)


_UUID = '11111111-2222-3333-4444-555555555555'
_PARAMS = {
    'public_key': 'p' * 43,
    'server_name': 'example.test',
    'short_ids': ['aabb'],
    'port': 443,
}
_GRPC = dict(
    SUBSCRIPTION_GRPC_ENABLED=True,
    SUBSCRIPTION_GRPC_PORT=80,
    SUBSCRIPTION_GRPC_SERVICE_NAME='google',
    SUBSCRIPTION_GRPC_PUBLIC_KEY='g' * 43,
    SUBSCRIPTION_GRPC_SERVER_NAME='google.com',
    SUBSCRIPTION_GRPC_SHORT_ID='6baeca16fb15cc',
)
_XHTTP = dict(
    SUBSCRIPTION_XHTTP_ENABLED=True,
    SUBSCRIPTION_XHTTP_PATH='/assets/v1/abcdef',
    SUBSCRIPTION_XHTTP_PORT=443,
)


class CascadeTests(SimpleTestCase):
    def test_each_stage_falls_back_to_the_next_one_through_a_loopback(self):
        loops, balancers, rules, entry = _cascade(
            [('a', ['x']), ('b', ['y']), ('c', ['z'])], 'T')

        self.assertEqual(entry, ('balancerTag', 'a'))
        self.assertEqual([balancer['fallbackTag'] for balancer in balancers[:-1]],
                         ['LOOP-T-L2', 'LOOP-T-L3'])
        # Последней ступени падать некуда: fallbackTag на несуществующий тег
        # заставил бы Xray отвергнуть весь документ.
        self.assertNotIn('fallbackTag', balancers[-1])

        self.assertEqual([loop['protocol'] for loop in loops], ['loopback', 'loopback'])
        self.assertEqual([loop['settings']['inboundTag'] for loop in loops],
                         ['T-L2-REROUTE', 'T-L3-REROUTE'])
        # Правило петли ведёт в следующий балансировщик, иначе трафик,
        # вернувшийся в роутинг, ушёл бы в ту же ступень по кругу.
        self.assertEqual([(rule['inboundTag'][0], rule['balancerTag']) for rule in rules],
                         [('T-L2-REROUTE', 'b'), ('T-L3-REROUTE', 'c')])

    def test_a_single_endpoint_skips_the_balancer_entirely(self):
        """Балансировщику над одним кандидатом нечего решать, но он может не решить ничего."""
        loops, balancers, rules, entry = _cascade([('only', ['x'])], 'T')

        self.assertEqual((loops, balancers, rules), ([], [], []))
        self.assertEqual(entry, ('outboundTag', 'x'))

    def test_a_single_stage_with_several_endpoints_still_balances(self):
        loops, balancers, rules, entry = _cascade([('only', ['x', 'y'])], 'T')

        self.assertEqual(entry, ('balancerTag', 'only'))
        self.assertEqual((loops, rules), ([], []))
        self.assertNotIn('fallbackTag', balancers[0])


@override_settings(**_GRPC, **_XHTTP)
class OwnProfileTests(SimpleTestCase):
    def test_transports_are_ordered_by_decision_not_by_latency(self):
        document = _build_xray_json(_UUID, _PARAMS, 'sub.example.test', 443, '', 0, '')

        balancers = document['routing']['balancers']
        self.assertEqual([balancer['selector'] for balancer in balancers],
                         [['proxy-nl-direct'], ['proxy-xhttp'], ['proxy-grpc']])
        # Плоский leastPing выбрал бы быстрейший, а нужен порядок попыток.
        self.assertNotIn('leastPing', str(balancers))

    def test_catch_all_rule_enters_the_first_stage(self):
        document = _build_xray_json(_UUID, _PARAMS, 'sub.example.test', 443, '', 0, '')

        catch_all = document['routing']['rules'][-1]
        self.assertEqual(catch_all['balancerTag'], 'own-l1')
        self.assertEqual(catch_all['network'], 'tcp,udp')

    def test_loop_rules_precede_the_country_rules(self):
        """Иначе трафик второй ступени ушёл бы в direct по правилу geoip:ru."""
        rules = _build_xray_json(_UUID, _PARAMS, 'sub.example.test', 443, '', 0, '')['routing']['rules']

        first_loop = next(i for i, rule in enumerate(rules) if 'inboundTag' in rule)
        first_direct = next(i for i, rule in enumerate(rules) if rule.get('outboundTag') == 'direct')
        self.assertLess(first_loop, first_direct)

    def test_relay_shares_the_first_stage_with_direct(self):
        document = _build_xray_json(_UUID, _PARAMS, 'sub.example.test', 443, 'relay.test', 443, '')

        self.assertEqual(document['routing']['balancers'][0]['selector'],
                         ['proxy-nl-direct', 'proxy-ru-relay'])

    def test_profile_is_named(self):
        document = _build_xray_json(_UUID, _PARAMS, 'sub.example.test', 443, '', 0, '')

        self.assertIn('Нидерланды', document['remarks'])


class LinkToOutboundTests(SimpleTestCase):
    def test_reality_vless_keeps_every_field_needed_to_dial(self):
        link = ('vless://' + _UUID + '@13.143.214.1:443?type=tcp&security=reality'
                '&pbk=KEY&fp=firefox&sni=cloudrynth.com&spx=%2F#x')

        outbound = _xray_outbound_from_link(link, 't0')

        self.assertEqual(outbound['protocol'], 'vless')
        vnext = outbound['settings']['vnext'][0]
        self.assertEqual((vnext['address'], vnext['port']), ('13.143.214.1', 443))
        reality = outbound['streamSettings']['realitySettings']
        self.assertEqual(reality['serverName'], 'cloudrynth.com')
        self.assertEqual(reality['fingerprint'], 'firefox')
        # Провайдер не задаёт shortId, и пустое поле Reality не принимает.
        self.assertNotIn('shortId', reality)

    def test_hysteria_link_becomes_the_fork_only_section(self):
        link = 'hy2://' + _UUID + '@13.143.214.1:25443/?sni=cloudrynth.com&alpn=h3#x'

        outbound = _xray_outbound_from_link(link, 't1')

        self.assertEqual(outbound['protocol'], 'hysteria')
        self.assertEqual(outbound['settings']['port'], 25443)
        self.assertEqual(outbound['streamSettings']['hysteriaSettings']['auth'], _UUID)
        self.assertEqual(outbound['streamSettings']['tlsSettings']['alpn'], ['h3'])

    def test_xhttp_carries_no_mux(self):
        link = ('vless://' + _UUID + '@sub.example.test:443?type=xhttp&security=tls'
                '&sni=sub.example.test&path=%2Fassets%2Fv1%2Fx&host=sub.example.test#x')

        outbound = _xray_outbound_from_link(link, 't4')

        # XHTTP мультиплексирует сам; Mux.Cool поверх него рвёт соединение сразу
        # после установки, и клиент получает EOF на первом же запросе.
        self.assertNotIn('mux', outbound)
        self.assertEqual(outbound['streamSettings']['xhttpSettings']['path'], '/assets/v1/x')

    def test_flowless_tcp_keeps_mux(self):
        link = ('vless://' + _UUID + '@13.143.214.1:443?type=tcp&security=reality'
                '&pbk=KEY&sni=cloudrynth.com#x')

        outbound = _xray_outbound_from_link(link, 't5')

        self.assertTrue(outbound['mux']['enabled'])

    def test_reality_without_a_public_key_is_refused(self):
        link = 'vless://' + _UUID + '@1.2.3.4:443?type=tcp&security=reality&sni=a.test#x'

        self.assertIsNone(_xray_outbound_from_link(link, 't2'))

    def test_unknown_scheme_is_refused(self):
        self.assertIsNone(_xray_outbound_from_link('ss://x@1.2.3.4:443#x', 't3'))


class MirrorProfileTests(SimpleTestCase):
    LINKS = [
        'vless://' + _UUID + '@13.143.214.1:443?type=tcp&security=reality&pbk=KEY'
        '&fp=firefox&sni=cloudrynth.com#%F0%9F%87%B3%F0%9F%87%B4%20%D0%9D%D0%BE%D1%80%D0%B2%D0%B5%D0%B3%D0%B8%D1%8F',
        'hy2://' + _UUID + '@13.143.214.1:25443/?sni=cloudrynth.com&alpn=h3'
        '#%F0%9F%87%B3%F0%9F%87%B4%20%D0%9D%D0%BE%D1%80%D0%B2%D0%B5%D0%B3%D0%B8%D1%8F%20%D0%B7%D0%B0%D0%BF%D0%B0%D1%81%D0%BD%D0%BE%D0%B9%20%D0%BF%D1%83%D1%82%D1%8C',
    ]

    def test_one_profile_per_country_with_both_transports_as_stages(self):
        profiles = _mirror_xray_profiles(self.LINKS, allow_hysteria=True)

        self.assertEqual(len(profiles), 1)
        profile = profiles[0]
        self.assertIn('Норвегия', profile['remarks'])
        # Транспортный суффикс не создаёт вторую страну.
        self.assertNotIn('запасной путь', profile['remarks'])
        protocols = [outbound['protocol'] for outbound in profile['outbounds']]
        self.assertEqual(protocols[:2], ['vless', 'hysteria'])
        self.assertEqual(profile['routing']['balancers'][0]['fallbackTag'], 'LOOP-M0-L2')

    def test_client_that_cannot_read_hysteria_gets_the_country_without_it(self):
        profiles = _mirror_xray_profiles(self.LINKS, allow_hysteria=False)

        self.assertEqual(len(profiles), 1)
        profile = profiles[0]
        protocols = [outbound['protocol'] for outbound in profile['outbounds']]
        self.assertNotIn('hysteria', protocols)
        # Осталась одна точка — маршрут ведёт прямо в неё, без балансировщика,
        # которому нечего выбирать, и без замеров, которых он бы ждал.
        self.assertEqual(profile['routing']['balancers'], [])
        self.assertNotIn('burstObservatory', profile)
        self.assertEqual(profile['routing']['rules'][-1]['outboundTag'], 'M0-s0')

    def test_every_profile_carries_its_own_direct_and_block(self):
        profile = _mirror_xray_profiles(self.LINKS, allow_hysteria=True)[0]

        tags = [outbound['tag'] for outbound in profile['outbounds']]
        self.assertEqual(tags[-2:], ['direct', 'block'])

    def test_the_profile_does_not_redefine_dns_at_all(self):
        """Со своей dns-секцией профиль пинговался, но трафик не шёл."""
        profile = _mirror_xray_profiles(self.LINKS, allow_hysteria=True)[0]

        self.assertNotIn('dns', profile)
        self.assertNotIn('dns-out', str(profile))
        self.assertNotIn('1.1.1.1/32', str(profile['routing']['rules']))

    def test_single_candidate_stage_has_no_latency_threshold(self):
        """maxRTT отбросил бы незамеренного кандидата, и ступень осталась бы пустой."""
        profile = _mirror_xray_profiles(self.LINKS, allow_hysteria=True)[0]

        for balancer in profile['routing']['balancers']:
            self.assertNotIn('maxRTT', balancer['strategy']['settings'])

    def test_observatory_watches_only_this_profile_s_own_endpoints(self):
        profile = _mirror_xray_profiles(self.LINKS, allow_hysteria=True)[0]

        self.assertEqual(profile['burstObservatory']['subjectSelector'], ['M0'])


class HysteriaCapabilityTests(SimpleTestCase):
    def test_happ_reads_the_fork_only_section(self):
        self.assertTrue(_wants_hysteria_outbound('Happ/2.9.0'))

    def test_v2rayng_does_not(self):
        """На незнакомом протоколе v2rayNG отвергает документ целиком."""
        self.assertFalse(_wants_hysteria_outbound('v2rayNG/1.9.16'))
