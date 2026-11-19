"""
Runs searches through both backends on a real OpenSearch node and checks
they agree, for the query shapes whose semantics are easiest to get wrong
(substring q, short q, accents, digits, sort collation, polygons).
"""
from django.contrib.gis.geos import Point
from django.http import QueryDict
from django.test import TestCase

from api.facility_search.rebuild import rebuild
from api.facility_search.search import execute, plan_search
from api.models.facility.facility_index import FacilityIndex
from api.tests.facility_search_helpers import FacilityFactoryMixin
from api.tests.opensearch_live import LiveIndexMixin, requires_opensearch

NAMES = [
    'Textile Plant 3', 'Plant 7 Textiles', 'Café Nguyễn', 'a b', 'A-B',
    'Ab', 'AB', 'ab', 'Zeta Knitwear', '3M Plant', 'Ünal Tekstil',
    '(Bracketed) Mill', 'Mill 30', 'Фабрика Б', '中国工厂', 'École Ab',
]

QUERIES = [
    '', 'q=textile', 'q=TEX', 'q=plant 3', 'q=plant 7', 'q=nguyen',
    'q=cafe', 'q=ab', 'q=a', 'q=3', 'q=unal', 'q=mill 3', 'q=-b',
    'q=Б', 'q=б', 'q=中', 'q=中国', 'q=é', 'q=E', 'q=ÉC', 'q=фаб',
    'sort_by=name_asc', 'sort_by=name_desc', 'sort_by=contributors_asc',
    'countries=TR', 'countries=VN&countries=TR',
    'boundary={"type":"Polygon","coordinates":'
    '[[[-0.5,-0.5],[4.5,-0.5],[4.5,4.5],[-0.5,4.5],[-0.5,-0.5]]]}',
]


@requires_opensearch
class LiveParityTest(LiveIndexMixin, FacilityFactoryMixin, TestCase):
    def setUp(self):
        super().setUp()
        countries = ['US', 'VN', 'TR']
        for i, name in enumerate(NAMES):
            self.create_facility(name=name, country_code=countries[i % 3],
                                 location=Point(i + 0.25, i / 2))
        rebuild(self.client, replicas=0, report=lambda _: None)

    def legacy(self, params):
        sort_by = params.get('sort_by')
        order = {
            None: ['-contributors_count', 'name', 'id'],
            'name_asc': ['name', 'id'],
            'name_desc': ['-name', 'id'],
            'contributors_asc': ['contributors_count', 'name', 'id'],
        }[sort_by]
        queryset = FacilityIndex.objects.filter_by_query_params(params)
        return list(queryset.order_by(*order).values_list('id', flat=True))

    def test_both_backends_agree(self):
        for query in QUERIES:
            params = QueryDict(query)
            with self.subTest(query):
                plan = plan_search(params, params.get('sort_by'), 0, 50)
                result = execute(self.client, plan, timeout=10)
                expected = self.legacy(params)
                self.assertEqual(result.total, len(expected))
                self.assertEqual(result.ids, expected)
