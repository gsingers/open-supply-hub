from unittest.mock import MagicMock

from django.contrib.gis.geos import Point
from django.test import SimpleTestCase, TestCase

from api.facility_search.parity import compare_query, parse_query, run
from api.facility_search.rebuild import rebuild
from api.tests.facility_search_helpers import FacilityFactoryMixin
from api.tests.opensearch_live import LiveIndexMixin, requires_opensearch


class ParseQueryTest(SimpleTestCase):
    def test_accepts_urls_paths_and_bare_query_strings(self):
        self.assertEqual(parse_query('q=a&countries=US'), 'q=a&countries=US')
        self.assertEqual(parse_query('?q=a'), 'q=a')
        self.assertEqual(parse_query('/api/facilities/?q=a'), 'q=a')
        self.assertEqual(
            parse_query('https://example.org/api/facilities/?q=a'), 'q=a')
        self.assertIsNone(parse_query('# comment'))
        self.assertIsNone(parse_query('   '))


class CompareQueryTest(FacilityFactoryMixin, TestCase):
    def test_routed_queries_are_reported_not_run(self):
        client = MagicMock()

        comparison = compare_query(client, 'embed=1&q=a')

        self.assertEqual(comparison.routed, 'embed')
        client.search.assert_not_called()

    def test_disagreement_is_counted_per_param(self):
        facility = self.create_facility(name='Alpha')
        client = MagicMock()
        client.search.return_value = {
            'hits': {'total': {'value': 2}, 'hits': [{'_id': facility.id}]},
            'aggregations': {},
        }

        report = run(client, ['countries=US', 'countries=US&q=alp'])

        summary = report.by_param()
        self.assertEqual(summary['countries'].queries, 2)
        self.assertEqual(summary['countries'].count_mismatches, 2)
        self.assertEqual(summary['q'].count_mismatches, 1)
        self.assertEqual(summary['(all)'].extent_mismatches, 2)
        self.assertEqual(len(report.as_dict()['mismatches']), 2)


@requires_opensearch
class LiveParityHarnessTest(LiveIndexMixin, FacilityFactoryMixin, TestCase):
    def test_identical_backends_report_no_mismatch(self):
        for i, name in enumerate(['Alpha Mill', 'Beta Mill', 'Gamma']):
            self.create_facility(name=name, location=Point(i, i))
        rebuild(self.client, replicas=0, report=lambda _: None)

        report = run(self.client, ['?', 'q=mill', 'sort_by=name_desc',
                                   'partner_contributor=1'])

        summary = report.by_param()['(all)']
        self.assertEqual(summary.queries, 4)
        self.assertEqual(summary.routed, 1)
        self.assertEqual(summary.errors, 0)
        self.assertEqual(summary.count_mismatches, 0)
        self.assertEqual(summary.id_mismatches, 0)
        self.assertEqual(summary.extent_mismatches, 0)
