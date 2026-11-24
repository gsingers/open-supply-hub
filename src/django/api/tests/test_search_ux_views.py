from unittest.mock import patch

from django.core.cache import caches
from django.test import TestCase, override_settings
from opensearchpy.exceptions import ConnectionTimeout
from rest_framework import status
from rest_framework.test import APIClient

from api.facility_search import backend

FACETS = '/api/map-search/facets/'
SUGGEST = '/api/map-search/suggest/'

EMPTY_FACETS = {'aggregations': {
    '_total': {'doc_count': 0},
    'f_countries': {'doc_count': 0, 'top': {'buckets': []}},
}}


@override_settings(MAP_SEARCH_UX_ENABLED=False)
class SearchUxOffTest(TestCase):
    def test_endpoints_are_absent_while_the_feature_is_off(self):
        client = APIClient()
        self.assertEqual(client.get(FACETS).status_code,
                         status.HTTP_404_NOT_FOUND)
        self.assertEqual(client.get(SUGGEST + '?q=den').status_code,
                         status.HTTP_404_NOT_FOUND)

    def test_feature_flag_is_reported_without_a_switch_row(self):
        response = APIClient().get('/api-feature-flags/')
        self.assertIs(response.json()['map_search_ux'], False)


LOCAL_CACHES = {name: {
    'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
    'LOCATION': name,
} for name in ('default', 'view_cache', 'api_throttling', 'waffle')}


@override_settings(MAP_SEARCH_UX_ENABLED=True, CACHES=LOCAL_CACHES)
class SearchUxOnTest(TestCase):
    def setUp(self):
        caches['view_cache'].clear()
        backend.breaker._failures = 0
        backend.breaker._open_until = 0.0
        patcher = patch.object(backend, 'client')
        self.os = patcher.start()()
        self.addCleanup(patcher.stop)
        self.api = APIClient()

    def test_facets_answer_with_counts_and_cache_headers(self):
        self.os.search_template.return_value = EMPTY_FACETS

        response = self.api.get(FACETS + '?facet=countries')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.json()['available'])
        self.assertIn('max-age', response['Cache-Control'])
        body = self.os.search_template.call_args.kwargs
        self.assertEqual(body['params'],
                         {'search_pipeline': 'facilities-ux-search-v1'})

    def test_repeated_requests_are_served_from_cache(self):
        self.os.search_template.return_value = EMPTY_FACETS

        self.api.get(FACETS + '?facet=countries')
        self.api.get(FACETS + '?facet=countries')

        self.assertEqual(self.os.search_template.call_count, 1)

    def test_opensearch_failure_is_a_503_the_client_falls_back_on(self):
        self.os.search_template.side_effect = ConnectionTimeout(
            'timeout', 'timed out', None)

        response = self.api.get(FACETS)

        self.assertEqual(response.status_code,
                         status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertFalse(response.json()['available'])

    def test_unsupported_filters_answer_unavailable(self):
        response = self.api.get(FACETS + '?partner_contributor=1')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.json()['available'])
        self.os.search_template.assert_not_called()

    def test_short_queries_get_no_suggestions_and_no_search(self):
        response = self.api.get(SUGGEST + '?q=d')

        self.assertEqual(response.json()['facilities'], [])
        self.os.search_template.assert_not_called()

    def test_feature_flag_is_on(self):
        response = self.api.get('/api-feature-flags/')
        self.assertIs(response.json()['map_search_ux'], True)
