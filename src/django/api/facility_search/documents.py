"""Build `facilities` index documents from `api_facilityindex` rows."""
from typing import Iterable

from django.db import connection

from api.facility_search.index_definition import (
    INTEGER_ARRAY_FIELDS,
    KEYWORD_ARRAY_FIELDS,
)
from api.models.facility.facility_index import FacilityIndex

ROW_FIELDS = (
    'id',
    'name',
    'native_language_name',
    'processing_type',
    'contributors_count',
    'location',
    'is_closed',
    'search_version',
    *KEYWORD_ARRAY_FIELDS,
    *INTEGER_ARRAY_FIELDS,
)

# Rows written before migration 0285 have no search_version until their
# next write. Any write after that is versioned from the sequence (>= 1).
UNVERSIONED = 0


def indexable_rows():
    """The rows the legacy search reads: FacilityIndex minus candidates."""
    return FacilityIndex.objects.without_candidates().values(*ROW_FIELDS)


def fetch_rows(facility_ids: Iterable[str]) -> dict:
    return {
        row['id']: row
        for row in indexable_rows().filter(id__in=list(facility_ids))
    }


def build_document(row: dict) -> dict:
    location = row['location']
    document = {
        'os_id': row['id'],
        'name': row['name'],
        'native_language_name': row['native_language_name'] or [],
        'processing_type': row['processing_type'] or [],
        'contributors_count': row['contributors_count'],
        'location': {'lat': location.y, 'lon': location.x},
        'is_closed': row['is_closed'],
    }
    for field in KEYWORD_ARRAY_FIELDS + INTEGER_ARRAY_FIELDS:
        value = row[field]
        if field == 'country_code':
            document[field] = value
        else:
            document[field] = [v for v in (value or []) if v is not None]
    return document


def document_version(row: dict) -> int:
    version = row.get('search_version')
    return UNVERSIONED if version is None else version


def delete_version() -> int:
    """
    A version for deletes that is at least every committed row version,
    so a delete always wins over an earlier upsert of the same id, and a
    later re-insert (which takes a new sequence value) wins over it.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            'SELECT last_value FROM api_facilityindex_search_version_seq'
        )
        return cursor.fetchone()[0]
