from unittest.mock import MagicMock, patch

from django.contrib.gis.geos import Point
from django.test import TestCase
from opensearchpy.exceptions import ConnectionTimeout
from rest_framework import status
from waffle.testutils import override_switch

from api.facility_search import backend
from api.tests.facility_search_helpers import FacilityFactoryMixin

CLIENT = 'api.facility_search.backend.client'


def os_response(ids, total=None, extent=None, downloadable=None):
    aggregations = {'extent': {}}
    if extent:
        xmin, ymin, xmax, ymax = extent
        aggregations['extent'] = {'bounds': {
            'top_left': {'lat': ymax, 'lon': xmin},
            'bottom_right': {'lat': ymin, 'lon': xmax},
        }}
    if downloadable is not None:
        aggregations['downloadable'] = {
            'buckets': [{'key': 1, 'doc_count': 1}] if downloadable else []
        }
    return {
        'hits': {'total': {'value': len(ids) if total is None else total},
                 'hits': [{'_id': os_id} for os_id in ids]},
        'aggregations': aggregations,
    }


class FacilitiesListBackendTest(FacilityFactoryMixin, TestCase):
    def setUp(self):
        super().setUp()
        backend.breaker.record_success()
        self.addCleanup(setattr, backend, 'breaker', backend.CircuitBreaker())
        self.a = self.create_facility(name='Alpha', location=Point(1, 1))
        self.b = self.create_facility(name='Beta', location=Point(2, 2))
        self.c = self.create_facility(name='Gamma', location=Point(3, 3))
        self.client_mock = MagicMock()
        patcher = patch(CLIENT, return_value=self.client_mock)
        patcher.start()
        self.addCleanup(patcher.stop)

    def get(self, query=''):
        return self.client.get(f'/api/facilities/{query}')

    def ids(self, response):
        return [feature['id'] for feature in response.data['features']]

    def test_switch_off_serves_postgres(self):
        response = self.get('?q=a')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.client_mock.search.assert_not_called()

    @override_switch(backend.SEARCH_SWITCH, active=True)
    def test_opensearch_order_total_and_extent_are_served(self):
        self.client_mock.search.return_value = os_response(
            [self.c.id, self.a.id], total=75, extent=(1, 1, 3, 3)
        )

        response = self.get('?q=a&sort_by=name_desc')

        self.assertEqual(self.ids(response), [self.c.id, self.a.id])
        self.assertEqual(response.data['count'], 75)
        self.assertEqual(response.data['extent'], (1, 1, 3, 3))
        self.assertIsNotNone(response.data['next'])
        body = self.client_mock.search.call_args.kwargs['body']
        self.assertEqual(body['sort'][0], {'name.sort': 'desc'})
        self.assertEqual(body['from'], 0)
        self.assertEqual(body['size'], 50)

    @override_switch(backend.SEARCH_SWITCH, active=True)
    def test_ids_without_a_row_are_dropped(self):
        self.client_mock.search.return_value = os_response(
            ['US0000000000000', self.b.id]
        )

        self.assertEqual(self.ids(self.get()), [self.b.id])

    @override_switch(backend.SEARCH_SWITCH, active=True)
    def test_opensearch_error_falls_back_to_postgres(self):
        self.client_mock.search.side_effect = ConnectionTimeout(
            'TIMEOUT', 'slow', None)

        response = self.get('?sort_by=name_asc')

        self.assertEqual(self.ids(response), [self.a.id, self.b.id, self.c.id])
        self.assertEqual(response.data['count'], 3)

    @override_switch(backend.SEARCH_SWITCH, active=True)
    def test_unsupported_params_and_deep_pages_use_postgres(self):
        for query in ('?embed=1&contributors=1', '?partner_contributor=1',
                      '?page=201', '?page=last'):
            with self.subTest(query):
                self.get(query)
        self.client_mock.search.assert_not_called()

    @override_switch(backend.SEARCH_SWITCH, active=True)
    def test_repeated_failures_open_the_circuit(self):
        backend.breaker = backend.CircuitBreaker(threshold=2,
                                                 cooldown_seconds=60)
        self.client_mock.search.side_effect = ConnectionTimeout(
            'TIMEOUT', 'slow', None)

        for _ in range(3):
            self.get()

        self.assertEqual(self.client_mock.search.call_count, 2)

    @override_switch(backend.SEARCH_SWITCH, active=True)
    def test_anonymised_only_comes_from_the_aggregation(self):
        flagged = MagicMock(contributor_ids={self.contributor.id})
        self.client_mock.search.return_value = os_response(
            [self.a.id], downloadable=False)
        with patch('api.views.facility.facilities_view_set.'
                   'ContributorMaskingPolicy.flagged_contributors',
                   return_value=flagged), \
                patch('api.views.facility.facilities_view_set.'
                      'ContributorMaskingPolicy.for_facilities_api'):
            response = self.get()

        self.assertTrue(response.data['anonymised_only'])
        aggs = self.client_mock.search.call_args.kwargs['body']['aggs']
        self.assertEqual(aggs['downloadable']['terms']['exclude'],
                         [self.contributor.id])

    @override_switch(backend.SEARCH_SWITCH, active=True)
    def test_count_uses_opensearch_and_falls_back(self):
        self.client_mock.count.return_value = {'count': 1234}
        self.assertEqual(self.get('count/').data['count'], 1234)

        self.client_mock.count.side_effect = ConnectionTimeout(
            'TIMEOUT', 'slow', None)
        self.assertEqual(self.get('count/').data['count'], 3)

    @override_switch(backend.SHADOW_SWITCH, active=True)
    def test_shadow_serves_postgres_and_compares(self):
        self.client_mock.search.return_value = os_response(
            [self.a.id, self.b.id, self.c.id], extent=(1, 1, 3, 3))
        with patch('api.facility_search.shadow.submit') as submit:
            response = self.get('?sort_by=name_asc')

        self.assertEqual(self.ids(response), [self.a.id, self.b.id, self.c.id])
        plan, legacy, query = submit.call_args.args
        self.assertEqual(legacy.ids, [self.a.id, self.b.id, self.c.id])
        self.assertEqual(legacy.total, 3)

        from api.facility_search.shadow import compare
        report = compare(plan, legacy, query, client=self.client_mock)
        self.assertTrue(report['ids_match'])
        self.assertTrue(report['count_match'])
        self.assertTrue(report['extent_match'])
