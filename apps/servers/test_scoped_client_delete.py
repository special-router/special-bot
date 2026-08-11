from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

from utils.py3xui.async_api_inbound import AsyncInboundApi


class ScopedClientDeleteTests(IsolatedAsyncioTestCase):
    def api(self, reads):
        api = object.__new__(AsyncInboundApi)
        api.get_by_id = AsyncMock(side_effect=reads)
        api._post = AsyncMock()
        api._url = lambda endpoint: f'http://panel/{endpoint}'
        return api

    @staticmethod
    def inbound(*ids):
        return SimpleNamespace(settings=SimpleNamespace(
            clients=[SimpleNamespace(id=value) for value in ids],
        ))

    async def test_deletes_exact_uuid_from_selected_inbound_and_verifies(self):
        api = self.api([self.inbound('target'), self.inbound()])

        await api.delete_client_by_uuid(5, 'target')

        self.assertEqual(api.get_by_id.await_count, 2)
        self.assertIn('/5/delClient/target', api._post.await_args.args[0])

    async def test_refuses_missing_or_duplicate_ownership_without_delete(self):
        for inbound in (self.inbound(), self.inbound('target', 'target')):
            api = self.api([inbound])
            with self.assertRaisesRegex(RuntimeError, 'ownership'):
                await api.delete_client_by_uuid(5, 'target')
            api._post.assert_not_awaited()

    async def test_fails_if_client_remains_after_delete(self):
        api = self.api([self.inbound('target'), self.inbound('target')])
        with self.assertRaisesRegex(RuntimeError, 'verification'):
            await api.delete_client_by_uuid(5, 'target')
