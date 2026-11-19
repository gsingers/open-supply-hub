from django.contrib.gis.geos import Point
from django.test import TestCase

from api.facility_search.index_definition import building_alias, read_alias
from api.facility_search.indexer import FacilitySearchIndexer
from api.facility_search.rebuild import alias_indices, rebuild, swap_alias
from api.models import FacilitySearchOutbox
from api.tests.facility_search_helpers import FacilityFactoryMixin
from api.tests.opensearch_live import LiveIndexMixin, requires_opensearch


@requires_opensearch
class FacilitySearchRebuildTest(LiveIndexMixin, FacilityFactoryMixin,
                                TestCase):
    def setUp(self):
        super().setUp()
        self.facilities = [
            self.create_facility(name=f'Facility {i}', location=Point(i, i))
            for i in range(3)
        ]

    def documents(self):
        self.client.indices.refresh(index=read_alias())
        response = self.client.search(
            index=read_alias(), body={'size': 100, 'query': {'match_all': {}}}
        )
        return {hit['_id']: hit['_source']
                for hit in response['hits']['hits']}

    def test_rebuild_loads_every_row_and_points_the_alias(self):
        index = rebuild(self.client, replicas=0, report=lambda _: None)

        self.assertEqual(alias_indices(self.client, read_alias()), [index])
        self.assertEqual(alias_indices(self.client, building_alias()), [])
        self.assertEqual(set(self.documents()),
                         {f.id for f in self.facilities})
        self.assertFalse(FacilitySearchOutbox.objects.exists())

    def test_indexer_keeps_the_index_current(self):
        rebuild(self.client, replicas=0, report=lambda _: None)
        renamed, removed = self.facilities[0], self.facilities[1]
        renamed.name = 'Renamed Facility'
        renamed.save()
        removed_id = removed.id
        removed.delete()

        FacilitySearchIndexer(self.client).run(once=True)

        documents = self.documents()
        self.assertEqual(documents[renamed.id]['name'], 'Renamed Facility')
        self.assertNotIn(removed_id, documents)

    def test_second_rebuild_swaps_atomically_and_keeps_the_old_index(self):
        first = rebuild(self.client, replicas=0, report=lambda _: None)
        second = rebuild(self.client, replicas=0, report=lambda _: None)

        self.assertEqual(alias_indices(self.client, read_alias()), [second])
        self.assertTrue(self.client.indices.exists(index=first))

        swap_alias(self.client, first)
        self.assertEqual(alias_indices(self.client, read_alias()), [first])

    def test_stale_rebuild_copy_does_not_overwrite_a_newer_document(self):
        index = rebuild(self.client, replicas=0, report=lambda _: None)
        facility = self.facilities[2]
        facility.name = 'Newest'
        facility.save()
        FacilitySearchIndexer(self.client).run(once=True)

        self.client.index(
            index=index, id=facility.id, body={'os_id': facility.id,
                                               'name': 'Stale'},
            version=1, version_type='external_gte', ignore=[409],
        )

        self.assertEqual(self.documents()[facility.id]['name'], 'Newest')


@requires_opensearch
class FailedRebuildTest(LiveIndexMixin, FacilityFactoryMixin, TestCase):
    def test_failed_build_stops_receiving_indexer_writes(self):
        from unittest.mock import patch
        from api.facility_search.rebuild import RebuildError
        self.create_facility()

        with patch('api.facility_search.rebuild._bulk_load',
                   side_effect=RebuildError('boom')), \
                self.assertRaises(RebuildError):
            rebuild(self.client, replicas=0, report=lambda _: None)

        self.assertEqual(alias_indices(self.client, building_alias()), [])
