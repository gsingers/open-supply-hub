"""
The search UX against a real OpenSearch node: stored templates, search
pipelines, scored text, facet counts and typeahead on a small index.
"""
from django.contrib.gis.geos import Point
from django.http import QueryDict
from django.test import TestCase

from api.facility_search.rebuild import rebuild
from api.facility_search.search import execute, plan_search
from api.facility_search.ux import assets, facets, suggest, values
from api.tests.facility_search_helpers import FacilityFactoryMixin
from api.tests.opensearch_live import LiveIndexMixin, requires_opensearch

FACILITIES = [
    ('Denim Mills Limited', 'IN'),
    ('Blue Denim Works', 'IN'),
    ('Denim Mills Limited', 'BD'),   # same name, another site
    ('Ocean Knit Ltd', 'BD'),
    ('Harbour Garments', 'VN'),
    ('Northern Spinning', 'VN'),
]


@requires_opensearch
class SearchUxLiveTest(LiveIndexMixin, FacilityFactoryMixin, TestCase):
    def setUp(self):
        super().setUp()
        for i, (name, country) in enumerate(FACILITIES):
            self.create_facility(name=name, country_code=country,
                                 location=Point(i, i))
        rebuild(self.client, replicas=0, report=lambda _: None)
        assets.install(self.client)
        self.addCleanup(self.client.indices.delete,
                        index=f'{values.values_alias()}-*',
                        ignore_unavailable=True, allow_no_indices=True)
        values.rebuild(self.client, {'country': {
            'IN': 'India', 'BD': 'Bangladesh', 'VN': 'Viet Nam'}.get},
            replicas=0)

    def search(self, query_string, sort_by='relevance'):
        plan = plan_search(QueryDict(query_string), sort_by, 0, 50)
        return execute(self.client, plan, timeout=10)

    def names(self, result):
        from api.models.facility.facility_index import FacilityIndex
        rows = FacilityIndex.objects.in_bulk(result.ids)
        return [rows[i].name for i in result.ids]

    def test_word_matches_rank_and_are_highlighted(self):
        result = self.search('q=denim mills')

        self.assertEqual(self.names(result)[:2],
                         ['Denim Mills Limited', 'Denim Mills Limited'])
        first = result.highlights[result.ids[0]]
        self.assertIn(assets.HIGHLIGHT_PRE, first['name'])

    def test_typos_and_abbreviations_still_match(self):
        self.assertIn('Blue Denim Works', self.names(self.search('q=denin')))
        # 'ltd' and 'limited' are synonyms at search time.
        self.assertIn('Denim Mills Limited',
                      self.names(self.search('q=denim ltd')))

    def test_legacy_substring_matches_are_kept_below_word_matches(self):
        # 'arbou' is no word, but the legacy substring search finds it.
        self.assertEqual(self.names(self.search('q=arbou')),
                         ['Harbour Garments'])

    def test_facet_counts_are_disjunctive(self):
        params = QueryDict('countries=IN')
        chosen = facets.requested_facets(['countries'], embed=False)
        template = facets.template_params(params, None, chosen, 10)
        result = facets.run(self.client, self.index_name, template, chosen,
                            timeout=10)

        self.assertEqual(result['total'], 2)
        counts = {v['value']: v['count']
                  for v in result['facets']['countries']['values']}
        self.assertEqual(counts, {'IN': 2, 'BD': 2, 'VN': 2})

    def test_did_you_mean_on_a_misspelling(self):
        chosen = facets.requested_facets(['countries'], embed=False)
        template = facets.template_params(
            QueryDict('q=spinnning'), 'relevance', chosen, 10)
        result = facets.run(self.client, self.index_name, template, chosen,
                            timeout=10)

        self.assertEqual(result['did_you_mean']['text'], 'spinning')

    def test_typeahead_collapses_same_names_and_offers_filters(self):
        result = suggest.suggest(self.client, self.index_name,
                                 QueryDict(''), 'den', timeout=10)

        names = [f['name'] for f in result['facilities']]
        self.assertEqual(names.count('Denim Mills Limited'), 1)
        self.assertIn('Blue Denim Works', names)

        result = suggest.suggest(self.client, self.index_name,
                                 QueryDict(''), 'bangl', timeout=10)
        self.assertEqual(result['filters'][0]['label'], 'Bangladesh')
        self.assertEqual(result['filters'][0]['count'], 2)
