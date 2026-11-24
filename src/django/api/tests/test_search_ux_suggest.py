from unittest.mock import MagicMock

from django.http import QueryDict
from django.test import TestCase

from api.facility_search.ux import suggest


def candidates_response(*sources):
    return {'aggregations': {'kinds': {'buckets': [
        {'top': {'hits': {'hits': [{'_source': s} for s in sources]}}},
    ]}}}


class NormalizeQueryTest(TestCase):
    def test_latin_needs_two_characters_wide_scripts_one(self):
        self.assertIsNone(suggest.normalize_query('d'))
        self.assertEqual(suggest.normalize_query(' de '), 'de')
        self.assertEqual(suggest.normalize_query('布'), '布')
        self.assertIsNone(suggest.normalize_query(None))


class SuggestTest(TestCase):
    def setUp(self):
        self.client = MagicMock()
        self.client.search_template.side_effect = [
            # facilities (suggest template)
            {'hits': {'hits': [{'_source': {
                'os_id': 'IN2020', 'name': 'Denim Mill',
                'country_code': 'IN', 'contributors_count': 3,
            }}]}},
            # value candidates (values template)
            candidates_response(
                {'kind': 'country', 'value': 'IN', 'label': 'India'},
                {'kind': 'contributor', 'value': '9', 'label': 'Hidden'},
                {'kind': 'sector', 'value': 'Denim', 'label': 'Denim'},
            ),
        ]
        # Counts: India 5, Denim 0 (the hidden contributor is never sent).
        self.client.search.return_value = {'aggregations': {
            'c0': {'doc_count': 5}, 'c1': {'doc_count': 0},
        }}

    def run_suggest(self, query_string='countries=BD&sectors=Apparel'):
        return suggest.suggest(
            self.client, 'facilities', QueryDict(query_string), 'den',
            timeout=1, hidden_contributors={9})

    def test_facilities_and_values_with_counts_under_current_filters(self):
        result = self.run_suggest()

        self.assertEqual(result['facilities'][0]['os_id'], 'IN2020')
        self.assertEqual(result['filters'], [{
            'kind': 'country', 'param': 'countries', 'value': 'IN',
            'label': 'India', 'count': 5,
        }])

    def test_a_value_is_counted_with_its_own_kind_replaced(self):
        self.run_suggest()

        aggs = self.client.search.call_args.kwargs['body']['aggs']
        india = aggs['c0']['filter']['bool']['filter']
        # The country filter (BD) is replaced by IN; sectors stay.
        self.assertIn({'terms': {'sector': ['Apparel']}}, india)
        self.assertIn({'term': {'country_code': 'IN'}}, india)
        self.assertNotIn({'terms': {'country_code': ['BD']}}, india)

    def test_masked_contributors_are_never_suggested_or_counted(self):
        self.run_suggest()

        aggs = self.client.search.call_args.kwargs['body']['aggs']
        self.assertEqual(len(aggs), 2)

    def test_os_id_input_is_reported_for_direct_navigation(self):
        self.client.search_template.side_effect = [
            {'hits': {'hits': []}}, candidates_response(),
        ]
        result = suggest.suggest(self.client, 'facilities', QueryDict(''),
                                 'cn2019303bq3fzp', timeout=1)
        self.assertEqual(result['os_id'], 'CN2019303BQ3FZP')
