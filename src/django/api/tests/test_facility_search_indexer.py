from unittest.mock import MagicMock

from django.contrib.gis.geos import Point
from django.test import TestCase
from django.utils import timezone

from api.facility_search.indexer import (
    FacilitySearchIndexer,
    IndexingError,
    failed_ids,
    write_targets,
)
from api.models import FacilitySearchOutbox
from api.models.facility.facility_index import FacilityIndex
from api.tests.facility_search_helpers import FacilityFactoryMixin


def fake_client(aliases=None, bulk_response=None):
    client = MagicMock()
    client.indices.get_alias.return_value = (
        aliases if aliases is not None
        else {'facilities-v1': {'aliases': {'facilities': {}}}}
    )
    client.bulk.return_value = bulk_response or {'errors': False,
                                                 'items': []}
    return client


def sent_actions(client):
    return client.bulk.call_args.kwargs['body']


class WriteTargetsTest(TestCase):
    def test_read_and_building_indices_are_targets(self):
        client = fake_client({
            'facilities-v1': {'aliases': {'facilities': {}}},
            'facilities-v2': {'aliases': {'facilities-building': {}}},
            'facilities-v0': {'aliases': {}},
        })

        self.assertEqual(write_targets(client),
                         ['facilities-v1', 'facilities-v2'])


class FailedIdsTest(TestCase):
    def test_stale_versions_and_missing_deletes_are_settled(self):
        response = {'errors': True, 'items': [
            {'index': {'_id': 'A', 'status': 409}},
            {'delete': {'_id': 'B', 'status': 404}},
            {'index': {'_id': 'C', 'status': 400, 'error': 'mapping'}},
            {'index': {'_id': 'D', 'status': 201}},
        ]}

        self.assertEqual(failed_ids(response), {'C'})


class FacilitySearchIndexerTest(FacilityFactoryMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.facility = self.create_facility(
            name='Textile Plant 3', location=Point(10, 20)
        )

    def test_upserts_existing_rows_with_their_version(self):
        client = fake_client()

        handled = FacilitySearchIndexer(client).process_batch()

        self.assertEqual(handled, 1)
        meta, document = sent_actions(client)
        row = FacilityIndex.objects.get(id=self.facility.id)
        self.assertEqual(meta['index']['_index'], 'facilities-v1')
        self.assertEqual(meta['index']['_id'], self.facility.id)
        self.assertEqual(meta['index']['version'], row.search_version)
        self.assertEqual(meta['index']['version_type'], 'external_gte')
        self.assertEqual(document['name'], 'Textile Plant 3')
        self.assertEqual(document['location'], {'lat': 20.0, 'lon': 10.0})
        self.assertEqual(document['country_code'], 'US')
        self.assertEqual(document['contributors_count'],
                         row.contributors_count)
        self.assertFalse(FacilitySearchOutbox.objects.exists())

    def test_deletes_documents_of_removed_rows(self):
        facility_id = self.facility.id
        FacilitySearchOutbox.objects.all().delete()
        self.facility.delete()
        client = fake_client()

        FacilitySearchIndexer(client).process_batch()

        (action,) = sent_actions(client)
        self.assertEqual(action['delete']['_id'], facility_id)
        self.assertIsInstance(action['delete']['version'], int)

    def test_writes_to_every_target(self):
        client = fake_client({
            'facilities-v1': {'aliases': {'facilities': {}}},
            'facilities-v2': {'aliases': {'facilities-building': {}}},
        })

        FacilitySearchIndexer(client).process_batch()

        indices = [a['index']['_index'] for a in sent_actions(client)
                   if 'index' in a]
        self.assertEqual(indices, ['facilities-v1', 'facilities-v2'])

    def test_opensearch_error_keeps_entries_for_retry(self):
        client = fake_client()
        client.bulk.side_effect = ConnectionError('down')

        with self.assertRaises(ConnectionError):
            FacilitySearchIndexer(client).process_batch()

        entry = FacilitySearchOutbox.objects.get()
        self.assertIsNone(entry.claimed_at)

    def test_failed_documents_are_kept_and_reported(self):
        client = fake_client(bulk_response={'errors': True, 'items': [
            {'index': {'_id': self.facility.id, 'status': 400}},
        ]})

        with self.assertRaises(IndexingError):
            FacilitySearchIndexer(client).process_batch()

        self.assertTrue(FacilitySearchOutbox.objects.filter(
            facility_id=self.facility.id).exists())

    def test_without_an_index_entries_are_dropped(self):
        client = fake_client(aliases={})

        FacilitySearchIndexer(client).process_batch()

        client.bulk.assert_not_called()
        self.assertFalse(FacilitySearchOutbox.objects.exists())

    def test_live_claims_are_skipped_and_expired_claims_retaken(self):
        FacilitySearchOutbox.objects.update(claimed_at=timezone.now())
        client = fake_client()

        self.assertEqual(FacilitySearchIndexer(client).process_batch(), 0)
        self.assertEqual(
            FacilitySearchIndexer(client, lease_seconds=-1).process_batch(),
            1,
        )

    def test_run_backs_off_after_a_failure(self):
        client = fake_client()
        client.bulk.side_effect = [ConnectionError('down'),
                                   {'errors': False, 'items': []}]
        sleeps = []

        def sleep(seconds):
            sleeps.append(seconds)
            if len(sleeps) > 1:
                raise KeyboardInterrupt

        with self.assertRaises(KeyboardInterrupt):
            FacilitySearchIndexer(client, sleep=sleep).run()

        self.assertEqual(sleeps[0], 1.0)
        self.assertFalse(FacilitySearchOutbox.objects.exists())
