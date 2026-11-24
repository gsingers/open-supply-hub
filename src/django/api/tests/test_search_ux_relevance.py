import json

from django.http import QueryDict
from django.test import TestCase

from api.facility_search.search import plan_search
from api.facility_search.ux import assets, relevance
from api.facility_search.ux.text import scored_text, sort_key


def params(query_string):
    return QueryDict(query_string)


class WantsRelevanceTest(TestCase):
    def test_only_with_relevance_sort_and_real_text(self):
        self.assertTrue(relevance.wants_relevance(params('q=denim'),
                                                  'relevance'))
        self.assertFalse(relevance.wants_relevance(params('q=denim'),
                                                   'name_asc'))
        self.assertFalse(relevance.wants_relevance(params(''), 'relevance'))
        # An OS ID is an exact lookup, not text to score.
        self.assertFalse(relevance.wants_relevance(
            params('q=CN2019303BQ3FZP'), 'relevance'))

    def test_scored_text_is_trimmed_and_capped(self):
        self.assertEqual(scored_text(params('q=%20denim%20')), 'denim')
        self.assertEqual(len(scored_text(params('q=' + 'a' * 500))), 200)


class TemplateParamsTest(TestCase):
    def test_list_plan_uses_the_template_with_text_scored_not_filtered(self):
        plan = plan_search(params('q=denim&countries=IN'), 'relevance',
                           offset=50, size=50)

        self.assertIsNone(plan.body)
        template = plan.template_params
        self.assertEqual(template['from'], 50)
        self.assertEqual(template['q'], 'denim')
        self.assertTrue(template['has_text'])
        self.assertTrue(template['highlight'])
        self.assertEqual(template['sort'][0], '_score')
        self.assertEqual(template['filters'],
                         [{'terms': {'country_code': ['IN']}}])
        # Every legacy match stays reachable, below real word matches.
        fallback = template['text_fallback']['constant_score']
        self.assertEqual(fallback['boost'], 0.3)

    def test_other_sorts_keep_the_legacy_request(self):
        plan = plan_search(params('q=denim'), 'name_asc', 0, 50)
        self.assertIsNotNone(plan.body)
        self.assertIsNone(plan.template_params)


class HighlightsTest(TestCase):
    def test_first_fragment_per_field_with_short_field_names(self):
        hits = [
            {'_id': 'A', 'highlight': {
                'name_text': ['Denim Mill'],
                'parent_company_name.text': ['Denim Co'],
            }},
            {'_id': 'B'},
        ]
        self.assertEqual(relevance.parse_highlights(hits), {'A': {
            'name': 'Denim Mill',
            'parent_company_name': 'Denim Co',
        }})


class TemplateSourceTest(TestCase):
    def render(self, source, values):
        """Fills the mustache tags the way the tests need, to check JSON."""
        text = source
        for name, value in values.items():
            text = text.replace('{{#toJson}}%s{{/toJson}}' % name,
                                json.dumps(value))
            text = text.replace('{{%s}}' % name, str(value))
        for section in ('has_text', 'highlight', 'suggest'):
            text = text.replace('{{#%s}}' % section, '')
            text = text.replace('{{/%s}}' % section, '')
        return json.loads(text)

    def test_search_template_renders_to_valid_json(self):
        body = self.render(assets.SEARCH_TEMPLATE_SOURCE, {
            'from': 0, 'size': 10, 'filters': [], 'sort': ['_score'],
            'aggs': {}, 'q': 'denim',
            'text_fallback': {'match_none': {}},
        })
        self.assertEqual(body['query']['bool']['minimum_should_match'], 0)
        self.assertIn('did_you_mean', body['suggest'])

    def test_suggest_template_renders_to_valid_json(self):
        body = self.render(assets.SUGGEST_TEMPLATE_SOURCE, {
            'size': 5, 'filters': [], 'q': 'den', 'q_key': 'den',
        })
        self.assertEqual(body['size'], 5)

    def test_sort_key_matches_the_collation_normalizer(self):
        self.assertEqual(sort_key('Dénim-Mill Ltd.'), 'denimmillltd')
