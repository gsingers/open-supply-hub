"""Fixtures shared by the facilities search index tests."""
from django.contrib.gis.geos import Point
from django.db.models.signals import post_delete

from api.models import (
    Contributor,
    Facility,
    FacilityList,
    FacilityListItem,
    Source,
    User,
)
from api.signals import location_post_delete_handler_for_opensearch


class FacilityFactoryMixin:
    """Creates confirmed facilities; the api_facility triggers index them."""

    def setUp(self):
        super().setUp()
        post_delete.disconnect(
            location_post_delete_handler_for_opensearch, Facility
        )
        self.addCleanup(
            post_delete.connect,
            location_post_delete_handler_for_opensearch,
            Facility,
        )
        user = User.objects.create(email='search-index@example.com')
        self.contributor = Contributor.objects.create(
            admin=user,
            name='search index contributor',
            contrib_type=Contributor.OTHER_CONTRIB_TYPE,
        )
        facility_list = FacilityList.objects.create(
            header='header', file_name='one', name='List'
        )
        self.source = Source.objects.create(
            facility_list=facility_list,
            source_type=Source.LIST,
            is_active=True,
            is_public=True,
            contributor=self.contributor,
        )
        self.next_row_index = 0

    def create_facility(self, name='Facility', country_code='US',
                        location=None, **kwargs):
        self.next_row_index += 1
        location = location or Point(0, 0)
        item = FacilityListItem.objects.create(
            name=name,
            address='Address',
            country_code=country_code,
            sector=['Apparel'],
            row_index=self.next_row_index,
            geocoded_point=location,
            status=FacilityListItem.CONFIRMED_MATCH,
            source=self.source,
        )
        return Facility.objects.create(
            name=name,
            address='Address',
            country_code=country_code,
            location=location,
            created_from=item,
            **kwargs,
        )
