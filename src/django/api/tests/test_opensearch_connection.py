from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from api.services.opensearch.opensearch import OpenSearchServiceConnection


@override_settings(DEBUG=True)
class OpenSearchServiceConnectionTest(SimpleTestCase):
    def setUp(self):
        OpenSearchServiceConnection._instance = None
        self.addCleanup(setattr, OpenSearchServiceConnection, '_instance',
                        None)

    @override_settings(
        OPENSEARCH_TIMEOUT_SECONDS=3.5,
        OPENSEARCH_MAX_RETRIES=4,
        OPENSEARCH_POOL_MAXSIZE=12,
    )
    def test_client_is_built_with_timeout_retries_and_pool(self):
        with patch('api.services.opensearch.opensearch.OpenSearch') as cls:
            OpenSearchServiceConnection()

        kwargs = cls.call_args.kwargs
        self.assertEqual(kwargs['timeout'], 3.5)
        self.assertEqual(kwargs['max_retries'], 4)
        self.assertTrue(kwargs['retry_on_timeout'])
        self.assertEqual(kwargs['pool_maxsize'], 12)

    def test_connection_is_a_singleton(self):
        with patch('api.services.opensearch.opensearch.OpenSearch') as cls:
            first = OpenSearchServiceConnection()
            second = OpenSearchServiceConnection()

        self.assertIs(first, second)
        cls.assert_called_once()
