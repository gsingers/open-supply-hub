from unittest.mock import patch

from django.test import SimpleTestCase, override_settings
from opensearchpy import OpenSearch
from opensearchpy.connection import Connection
from opensearchpy.exceptions import ConnectionTimeout

from api.services.opensearch.opensearch import (
    OpenSearchServiceConnection,
    client_options,
)


class TimeoutConnection(Connection):
    """Every request times out; counts how often it was tried."""
    attempts = 0

    def perform_request(self, *args, **kwargs):
        TimeoutConnection.attempts += 1
        raise ConnectionTimeout('TIMEOUT', 'timed out', None)


def attempts_for(options):
    TimeoutConnection.attempts = 0
    client = OpenSearch(hosts=[{'host': 'localhost', 'port': 9200}],
                        connection_class=TimeoutConnection, **options)
    try:
        client.search(index='facilities', body={}, request_timeout=2)
    except ConnectionTimeout:
        pass
    return TimeoutConnection.attempts


@override_settings(DEBUG=True)
class OpenSearchServiceConnectionTest(SimpleTestCase):
    def setUp(self):
        OpenSearchServiceConnection._instance = None
        self.addCleanup(setattr, OpenSearchServiceConnection, '_instance',
                        None)

    def test_default_client_keeps_the_library_timeout_and_retries(self):
        # v1 search and rebuild callers relied on opensearch-py's defaults.
        options = client_options()

        self.assertEqual(options['timeout'], 10)
        self.assertFalse(options['retry_on_timeout'])
        self.assertEqual(options['max_retries'], 3)

    def test_map_client_never_retries(self):
        options = client_options(for_map=True)

        self.assertEqual(attempts_for(options), 1)

    def test_default_client_does_not_retry_timeouts(self):
        self.assertEqual(attempts_for(client_options()), 1)

    @override_settings(OPENSEARCH_POOL_MAXSIZE=12)
    def test_clients_are_built_with_a_sized_pool(self):
        with patch('api.services.opensearch.opensearch.OpenSearch') as cls:
            connection = OpenSearchServiceConnection()
            connection.map_client

        pools = [call.kwargs['pool_maxsize'] for call in cls.call_args_list]
        self.assertEqual(pools, [12, 12])
        self.assertEqual(cls.call_args_list[1].kwargs['max_retries'], 0)

    def test_connection_is_a_singleton(self):
        with patch('api.services.opensearch.opensearch.OpenSearch') as cls:
            first = OpenSearchServiceConnection()
            second = OpenSearchServiceConnection()

        self.assertIs(first, second)
        cls.assert_called_once()
