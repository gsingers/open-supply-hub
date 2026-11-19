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
    def test_stale_ids_send_the_request_to_postgres(self):
        self.client_mock.search.return_value = os_response(
            ['US0000000000000', self.b.id], total=40
        )

        response = self.get('?sort_by=name_asc')

        self.assertEqual(self.ids(response),
                         [self.a.id, self.b.id, self.c.id])
        self.assertEqual(response.data['count'], 3)

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
        with patch('api.facility_search.shadow.submit') as submit, \
                patch('api.facility_search.shadow.sampled',
                      return_value=True):
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


class HydrateCandidatesTest(FacilityFactoryMixin, TestCase):
    """A document for a facility that is now a candidate (OSDEV-3243)."""

    def setUp(self):
        super().setUp()
        self.addCleanup(setattr, backend, 'breaker', backend.CircuitBreaker())
        self.a = self.create_facility(name='Alpha')
        self.candidate = self.create_facility(name='Now a candidate')
        # Flip it, then put its FacilityIndex row back: a stale row, as
        # the consumer contract tests use.
        from api.models import Facility
        from api.models.facility.facility_index import FacilityIndex
        row = FacilityIndex.objects.get(id=self.candidate.id)
        Facility.including_candidates.filter(id=self.candidate.id).update(
            is_candidate=True)
        row.save(force_insert=True)
        self.client_mock = MagicMock()
        self.client_mock.search.return_value = os_response(
            [self.candidate.id, self.a.id])
        patcher = patch(CLIENT, return_value=self.client_mock)
        patcher.start()
        self.addCleanup(patcher.stop)

    @override_switch(backend.SEARCH_SWITCH, active=True)
    def test_candidates_are_never_served_and_the_page_stays_consistent(self):
        from api.models import FacilitySearchOutbox
        FacilitySearchOutbox.objects.all().delete()

        response = self.client.get('/api/facilities/')

        ids = [feature['id'] for feature in response.data['features']]
        self.assertEqual(ids, [self.a.id])
        self.assertEqual(response.data['count'], 1)
        # The stale document is handed to the indexer to settle.
        self.assertTrue(FacilitySearchOutbox.objects.filter(
            facility_id=self.candidate.id).exists())


class ShadowQueueTest(TestCase):
    def test_samples_are_dropped_when_the_queue_is_full(self):
        from django.conf import settings
        from api.facility_search import shadow
        self.assertLessEqual(settings.MAP_OPENSEARCH_SHADOW_SAMPLE_RATE, 0.1)
        with patch.object(shadow, '_executor') as executor, \
                patch.object(shadow, '_slots') as slots:
            slots.acquire.return_value = False

            self.assertIsNone(shadow.submit(MagicMock(), MagicMock(), 'q'))
            executor.submit.assert_not_called()

    def test_a_slot_is_released_when_the_comparison_ends(self):
        from api.facility_search import shadow
        free_before = shadow._slots._value
        with patch.object(shadow, 'compare', return_value={}):
            future = shadow.submit(MagicMock(), MagicMock(), 'q')
            future.result(timeout=5)

        self.assertEqual(shadow._slots._value, free_before)
