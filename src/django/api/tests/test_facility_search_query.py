from django.http import QueryDict
from django.test import TestCase

from api.facility_search.query import (
    MATCH_NONE,
    UnsupportedQuery,
    build_filters,
    build_sort,
)
from api.models import FacilityAlias
from api.tests.facility_search_helpers import FacilityFactoryMixin


def filters(query_string):
    return build_filters(QueryDict(query_string))


class UnsupportedParamsTest(TestCase):
    def test_params_without_parity_are_routed_to_postgres(self):
        for query_string in (
            'embed=1',
            'embed=0&q=shirt',
            'partner_contributor=12',
            'facility_type=zzzz unknown words',
            'native_language_name=a,b',
            'boundary={"type":"MultiPolygon","coordinates":'
            '[[[[0,0],[1,0],[1,1],[0,0]]]]}',
        ):
            with self.subTest(query_string):
                with self.assertRaises(UnsupportedQuery):
                    filters(query_string)


class FreeTextTest(TestCase):
    def test_q_is_a_substring_match_or_an_exact_id(self):
        (clause,) = filters('q=Textile')

        self.assertEqual(clause, {'bool': {'should': [
            {'match_phrase': {'name.trigram': 'Textile'}},
            {'term': {'os_id': 'Textile'}},
        ], 'minimum_should_match': 1}})

    def test_short_q_uses_a_folded_wildcard(self):
        (clause,) = filters('q=Ü*')

        self.assertEqual(clause['bool']['should'][0], {'wildcard': {
            'name.folded': {'value': '*u\\**', 'case_insensitive': True},
        }})

    def test_empty_q_matches_everything(self):
        self.assertEqual(filters('q='), [])

    def test_name_behaves_like_q(self):
        self.assertEqual(filters('name=Textile'), filters('q=Textile'))


class IdTest(FacilityFactoryMixin, TestCase):
    def test_os_id_shaped_q_resolves_aliases(self):
        facility = self.create_facility()
        FacilityAlias.objects.create(
            os_id='US2019000000000', facility=facility,
            reason=FacilityAlias.MERGE,
        )

        self.assertEqual(filters('q=US2019000000000'),
                         [{'term': {'os_id': facility.id}}])

    def test_retired_os_id_matches_nothing(self):
        FacilityAlias.objects.create(
            os_id='US2019000000001', facility=None,
            reason=FacilityAlias.NOT_A_FACILITY,
        )

        self.assertEqual(filters('id=US2019000000001'), [MATCH_NONE])


class TermFiltersTest(TestCase):
    def test_simple_overlap_filters(self):
        self.assertEqual(
            filters('countries=US&countries=CN&contributor_types=Brand'
                    '&lists=4&sectors=Apparel&number_of_workers=1001-5000'),
            [
                {'terms': {'country_code': ['US', 'CN']}},
                {'terms': {'contrib_types': ['Brand']}},
                {'terms': {'lists': ['4']}},
                {'terms': {'number_of_workers': ['1001-5000']}},
                {'terms': {'sector': ['Apparel']}},
            ],
        )

    def test_contributors_or_and_and(self):
        self.assertEqual(filters('contributors=1&contributors=2'),
                         [{'terms': {'contributors_id': ['1', '2']}}])
        self.assertEqual(
            filters('contributors=1&contributors=2&combine_contributors=AND'),
            [{'term': {'contributors_id': '1'}},
             {'term': {'contributors_id': '2'}}],
        )

    def test_parent_company_by_id_or_name(self):
        self.assertEqual(
            filters('parent_company=12&parent_company=Acme'),
            [{'bool': {'should': [
                {'terms': {'parent_company_id': ['12']}},
                {'terms': {'parent_company_name': ['Acme']}},
            ], 'minimum_should_match': 1}}],
        )

    def test_product_type_is_cleaned_like_legacy(self):
        self.assertEqual(filters('product_type=T-Shirts'),
                         [{'terms': {'product_type': ['tshirts']}}])


class TaxonomyFiltersTest(TestCase):
    def test_taxonomy_facility_type(self):
        self.assertEqual(
            filters('facility_type=Final Product Assembly'),
            [{'terms': {'facility_type': ['Final Product Assembly']}}],
        )

    def test_exact_processing_type_is_case_insensitive(self):
        self.assertEqual(
            filters('processing_type=Sewing&processing_type_exact=Sewing'),
            [{'terms': {'processing_type.lowercase': ['sewing']}}],
        )

    def test_blank_short_value_matches_nothing(self):
        self.assertEqual(filters('facility_type=ab'), [MATCH_NONE])


class IsicFiltersTest(TestCase):
    def test_isic_codes_match_any_column(self):
        (clause,) = filters('isic_4=section:C')

        columns = {
            next(iter(should['terms']))
            for should in clause['bool']['should']
        }
        self.assertIn('isic_section', columns)
        self.assertIn('isic_mapped_section', columns)
        self.assertIn('isic5_class', columns)

    def test_isic_without_valid_values_matches_nothing(self):
        self.assertEqual(filters('isic_4=nonsense'), [MATCH_NONE])


class OtherFiltersTest(TestCase):
    def test_native_language_name_is_a_case_insensitive_substring(self):
        self.assertEqual(
            filters('native_language_name=Фабрика'),
            [{'match_phrase': {'native_language_name.trigram': 'Фабрика'}}],
        )

    def test_boundary_polygon(self):
        boundary = ('{"type":"Polygon","coordinates":'
                    '[[[0,0],[1,0],[1,1],[0,0]]]}')
        (clause,) = filters(f'boundary={boundary}')

        self.assertEqual(clause['geo_shape']['location']['relation'],
                         'intersects')
        self.assertEqual(
            clause['geo_shape']['location']['shape']['type'], 'Polygon')


class SortTest(TestCase):
    def test_sorts_match_legacy_order_by(self):
        name = [{'name.sort': 'asc'}, {'name_tiebreak': 'asc'}]
        os_id = {'os_id': 'asc'}
        cases = {
            None: [{'contributors_count': 'desc'}, *name, os_id],
            'contributors_desc': [{'contributors_count': 'desc'}, *name,
                                  os_id],
            'contributors_asc': [{'contributors_count': 'asc'}, *name,
                                 os_id],
            'name_asc': [*name, os_id],
            'name_desc': [{'name.sort': 'desc'}, {'name_tiebreak': 'desc'},
                          os_id],
            'unknown': [os_id],
        }
        for sort_by, expected in cases.items():
            with self.subTest(sort_by):
                self.assertEqual(build_sort(sort_by), expected)
