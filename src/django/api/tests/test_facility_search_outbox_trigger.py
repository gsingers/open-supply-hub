from django.db import connection
from django.test import TestCase
from django.utils import timezone

from api.models import Facility, FacilitySearchOutbox
from api.models.facility.facility_index import FacilityIndex
from api.tests.facility_search_helpers import FacilityFactoryMixin


class FacilitySearchOutboxTriggerTest(FacilityFactoryMixin, TestCase):
    def pending(self, facility_id):
        return FacilitySearchOutbox.objects.filter(
            facility_id=facility_id, claimed_at__isnull=True
        )

    def test_indexing_a_facility_enqueues_it(self):
        facility = self.create_facility()

        self.assertEqual(self.pending(facility.id).count(), 1)

    def test_pending_entry_is_not_duplicated(self):
        facility = self.create_facility()
        facility.name = 'Renamed'
        facility.save()
        Facility.update_facility_updated_at_field(facility.id)

        self.assertEqual(
            FacilitySearchOutbox.objects.filter(
                facility_id=facility.id
            ).count(),
            1,
        )

    def test_change_after_claim_enqueues_again(self):
        facility = self.create_facility()
        self.pending(facility.id).update(claimed_at=timezone.now())

        facility.name = 'Renamed'
        facility.save()

        self.assertEqual(self.pending(facility.id).count(), 1)
        self.assertEqual(
            FacilitySearchOutbox.objects.filter(
                facility_id=facility.id
            ).count(),
            2,
        )

    def test_delete_enqueues(self):
        facility = self.create_facility()
        facility_id = facility.id
        FacilitySearchOutbox.objects.all().delete()

        facility.delete()

        self.assertFalse(FacilityIndex.objects.filter(id=facility_id).exists())
        self.assertEqual(self.pending(facility_id).count(), 1)

    def test_direct_sql_update_enqueues(self):
        facility = self.create_facility()
        FacilitySearchOutbox.objects.all().delete()

        with connection.cursor() as cursor:
            cursor.execute(
                'UPDATE api_facilityindex SET is_closed = TRUE '
                'WHERE id = %s',
                [facility.id],
            )

        self.assertEqual(self.pending(facility.id).count(), 1)

    def test_every_write_bumps_search_version(self):
        facility = self.create_facility()
        first = FacilityIndex.objects.get(id=facility.id).search_version

        FacilityIndex.objects.filter(id=facility.id).update(is_closed=True)

        second = FacilityIndex.objects.get(id=facility.id).search_version
        self.assertIsNotNone(first)
        self.assertGreater(second, first)
