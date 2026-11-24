from django.http import QueryDict
from django.test import TestCase

from api.facility_search.ux import facets


def params(query_string):
    return QueryDict(query_string)


def by_key(keys, embed=False):
    return facets.requested_facets(keys, embed)


class RequestedFacetsTest(TestCase):
    def test_all_known_facets_by_default_and_unknown_keys_dropped(self):
        self.assertEqual([f.key for f in by_key([])],
                         [f.key for f in facets.FACETS])
        self.assertEqual([f.key for f in by_key(['countries', 'nope'])],
                         ['countries'])

    def test_embed_never_counts_the_pinned_contributor(self):
        self.assertNotIn('contributors',
                         [f.key for f in by_key([], embed=True)])


class DisjunctiveAggsTest(TestCase):
    def setUp(self):
        self.template = facets.template_params(
            params('countries=IN&countries=BD&sectors=Apparel'),
            sort_by=None,
            facets=by_key(['countries', 'sectors', 'contributors']),
            size=10,
            allowed_contributors=[1, 2],
        )
        self.aggs = self.template['aggs']

    def test_total_applies_every_filter(self):
        clauses = self.aggs['_total']['filter']['bool']['filter']
        self.assertIn({'terms': {'country_code': ['IN', 'BD']}}, clauses)
        self.assertIn({'terms': {'sector': ['Apparel']}}, clauses)

    def test_a_facet_ignores_only_its_own_filter(self):
        country = self.aggs['f_countries']['filter']['bool']['filter']
        self.assertEqual(country, [{'terms': {'sector': ['Apparel']}}])
        sector = self.aggs['f_sectors']['filter']['bool']['filter']
        self.assertEqual(sector, [{'terms': {'country_code': ['IN', 'BD']}}])

    def test_selected_values_are_always_counted(self):
        self.assertEqual(
            self.aggs['f_countries']['aggs']['selected']['terms']['include'],
            ['IN', 'BD'],
        )
        self.assertNotIn('selected', self.aggs['f_contributors']['aggs'])

    def test_contributor_counts_are_limited_to_public_contributors(self):
        terms = self.aggs['f_contributors']['aggs']['top']['terms']
        self.assertEqual(terms['include'], [1, 2])

    def test_one_relaxation_per_active_group(self):
        self.assertEqual(
            sorted(k for k in self.aggs if k.startswith('r_')),
            ['r_countries', 'r_sectors'],
        )

    def test_unscored_text_is_a_filter_group_and_no_page_is_fetched(self):
        template = facets.template_params(
            params('q=denim'), None, by_key(['countries']), 10)
        self.assertFalse(template['has_text'])
        self.assertEqual(template['size'], 0)
        self.assertIn('r_text', template['aggs'])

    def test_relevance_scores_the_text_in_the_query(self):
        template = facets.template_params(
            params('q=denim'), 'relevance', by_key(['countries']), 10)
        self.assertTrue(template['has_text'])
        self.assertTrue(template['suggest'])
        self.assertNotIn('r_text', template['aggs'])

    def test_embed_never_offers_removing_the_pinned_contributor(self):
        template = facets.template_params(
            params('embed=1&contributors=7&countries=IN'), None,
            by_key([], embed=True), 10, [7])
        self.assertNotIn('r_contributors', template['aggs'])
        self.assertIn('r_countries', template['aggs'])


class ParseTest(TestCase):
    def test_counts_relaxations_and_did_you_mean(self):
        response = {
            'aggregations': {
                '_total': {'doc_count': 0},
                'f_countries': {
                    'doc_count': 12,
                    'top': {'buckets': [{'key': 'IN', 'doc_count': 9}],
                            'sum_other_doc_count': 3},
                    'selected': {'buckets': [
                        {'key': 'IN', 'doc_count': 9},
                        {'key': 'PE', 'doc_count': 0},
                    ]},
                },
                'r_countries': {'doc_count': 12},
                'r_sectors': {'doc_count': 40},
            },
            'suggest': {'did_you_mean': [{'options': [
                {'text': 'denim', 'highlighted': 'denim'},
            ]}]},
        }

        parsed = facets.parse(response, by_key(['countries']))

        self.assertEqual(parsed['total'], 0)
        self.assertEqual(parsed['facets']['countries'], {
            'values': [{'value': 'IN', 'count': 9}],
            'selected': [{'value': 'PE', 'count': 0}],
            'other': 3,
            'total': 12,
        })
        self.assertEqual(parsed['relaxations'], [
            {'group': 'sectors', 'count': 40},
            {'group': 'countries', 'count': 12},
        ])
        self.assertEqual(parsed['did_you_mean']['text'], 'denim')

    def test_no_suggestion_is_none(self):
        response = {'aggregations': {'_total': {'doc_count': 3}}}
        self.assertIsNone(facets.parse(response, [])['did_you_mean'])
