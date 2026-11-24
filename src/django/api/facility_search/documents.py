"""Build `facilities` index documents from `api_facilityindex` rows."""
from typing import Iterable

from django.db import connection
from django.db.models.expressions import RawSQL
from unidecode import unidecode

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
    'address',
    'approved_claim_ids',
    *KEYWORD_ARRAY_FIELDS,
    *INTEGER_ARRAY_FIELDS,
)

# Rows written before migration 0285 have no search_version until their
# next write. Any write after that is versioned from the sequence (>= 1).
UNVERSIONED = 0


def indexable_rows():
    """The rows the legacy search reads: FacilityIndex minus candidates."""
    return FacilityIndex.objects.without_candidates().annotate(
        name_search=RawSQL('UPPER(UNACCENT(api_facilityindex.name))', []),
        custom_text_folded=RawSQL(
            'UNACCENT(api_facilityindex.custom_text_search)', []),
    ).values(*ROW_FIELDS, 'name_search', 'custom_text_folded')


def fetch_rows(facility_ids: Iterable[str]) -> dict:
    return {
        row['id']: row
        for row in indexable_rows().filter(id__in=list(facility_ids))
    }


def collation_tiebreak(name: str) -> str:
    """
    Order names that are equal at the first level of the en_US.UTF-8
    collation (letters and digits, case- and accent-insensitive) the way
    it does: unaccented before accented, then lower case before upper
    case, then names with more spaces and punctuation first. An
    approximation of glibc's later levels, good for the common ties
    (case, hyphens, spaces); the parity harness reports what is left.
    """
    letters = [ch for ch in name if ch.isalnum()]
    accents = ''.join('1' if unidecode(ch) != ch else '0' for ch in letters)
    cases = ''.join('1' if ch.isupper() else '0' for ch in letters)
    punctuation = 99 - min(len(name) - len(letters), 99)
    return f'{accents}|{cases}|{punctuation:02d}'


def build_document(row: dict) -> dict:
    location = row['location']
    document = {
        'os_id': row['id'],
        'name': row['name'],
        'name_search': row['name_search'],
        'name_tiebreak': collation_tiebreak(row['name']),
        'native_language_name': row['native_language_name'] or [],
        'processing_type': row['processing_type'] or [],
        'contributors_count': row['contributors_count'],
        'location': {'lat': location.y, 'lon': location.x},
        'is_closed': row['is_closed'],
        'name_text': row['name'],
        'address_text': row.get('address') or '',
        'claim_status': ('claimed' if row.get('approved_claim_ids')
                         else 'unclaimed'),
        'custom_text_search': row.get('custom_text_folded') or '',
    }
    if row['contributors_count']:
        document['popularity'] = row['contributors_count']
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
