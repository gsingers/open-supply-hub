"""
Translate `/api/facilities/` query params into `facilities` index clauses.

Mirrors `FacilityIndexNewManager.filter_by_query_params` parameter by
parameter. Every clause goes into `bool.filter` (unscored, cacheable).
A param this module cannot answer with the same results as Postgres
raises `UnsupportedQuery`, and the caller serves the request from
Postgres instead.
"""
import json
import re
from typing import List, Optional

from django.contrib.gis.geos import GEOSException, GEOSGeometry, Polygon

from api.constants import FacilitiesQueryParams as P
from api.facility_search.index_definition import NGRAM_SIZE
from api.helpers.helpers import clean
from api.isic import isic_filter_columns, parse_isic_filter_values
from api.os_id import string_matches_os_id_format
from api.services.facility_processing_filter import FacilityProcessingFilter
from api.services.facility_processing_query import FacilityProcessingQuery

MATCH_NONE = {'match_none': {}}

# Postgres renders an array as {a,"b c"}; ILIKE on that text can match
# across these characters, which per-element matching cannot reproduce.
ARRAY_TEXT_SYNTAX = re.compile(r'[{}",\\]')
WILDCARD_SPECIAL = re.compile(r'([*?\\])')


class UnsupportedQuery(Exception):
    """The request needs a filter only Postgres can answer exactly."""


def _any_of(*clauses) -> dict:
    return {'bool': {'should': list(clauses), 'minimum_should_match': 1}}


def _substring(field: str, text: str, folded: bool) -> dict:
    """
    ILIKE '%text%' on a keyword with a trigram subfield. Short terms use a
    wildcard on the normalized keyword; the field's normalizer folds the
    pattern the same way as the indexed value (lowercase, and ASCII
    folding of accents for `folded`), which, like UNACCENT, leaves other
    scripts alone.
    """
    if len(text) >= NGRAM_SIZE:
        return {'match_phrase': {f'{field}.trigram': text}}
    subfield = 'folded' if folded else 'lowercase'
    return {'wildcard': {f'{field}.{subfield}': {
        'value': '*' + WILDCARD_SPECIAL.sub(r'\\\1', text.lower()) + '*',
        'case_insensitive': True,
    }}}


def _resolve_os_id(os_id: str) -> Optional[str]:
    from api.models.facility.facility_alias import FacilityAlias
    try:
        # A NOT_A_FACILITY tombstone has no facility, so a retired OS ID
        # matches nothing rather than redirecting (OSDEV-3246).
        return FacilityAlias.objects.get(pk=os_id).facility_id
    except FacilityAlias.DoesNotExist:
        return os_id


def _id_and_text(params) -> List[dict]:
    clauses = []
    os_id = params.get(P.ID)
    text = params.get(P.Q)
    if os_id is None and string_matches_os_id_format(text):
        os_id, text = text, None
    if os_id is not None:
        resolved = _resolve_os_id(os_id)
        clauses.append({'term': {'os_id': resolved}} if resolved
                       else MATCH_NONE)
    # `name` is the deprecated spelling of `q`; both may be given.
    for value in (text, params.get(P.NAME)):
        if value:
            clauses.append(_any_of(_substring('name', value, folded=True),
                                   {'term': {'os_id': value}}))
    return clauses


def _terms(field: str, values) -> List[dict]:
    return [{'terms': {field: list(values)}}] if values else []


def _contributors(params) -> List[dict]:
    contributors = params.getlist(P.CONTRIBUTORS)
    if not contributors:
        return []
    if params.get(P.COMBINE_CONTRIBUTORS, '').upper() == 'AND':
        return [{'term': {'contributors_id': c}} for c in contributors]
    return _terms('contributors_id', contributors)


def _boundary(params) -> List[dict]:
    boundary = params.get(P.BOUNDARY)
    if boundary is None:
        return []
    try:
        geometry = GEOSGeometry(boundary)
    except (GEOSException, ValueError) as error:
        raise UnsupportedQuery(f'boundary: {error}')
    if not isinstance(geometry, Polygon) or geometry.num_interior_rings:
        raise UnsupportedQuery('boundary: only simple polygons')
    # Points on the polygon edge match here but not in ST_Within.
    return [{'geo_shape': {'location': {
        'shape': json.loads(geometry.geojson), 'relation': 'intersects',
    }}}]


def _parent_company(params) -> List[dict]:
    values = params.getlist(P.PARENT_COMPANY)
    if not values:
        return []
    ids = [v for v in values if v.isnumeric()]
    names = [v for v in values if not v.isnumeric()]
    return [_any_of(*_terms('parent_company_id', ids),
                    *_terms('parent_company_name', names))]


def _taxonomy_param(classified, field: str, slot: int) -> dict:
    values = []
    for classification, data in classified:
        if classification == 'free_text':
            raise UnsupportedQuery(f'{field}: free text')
        if classification == 'taxonomy' and data[slot] is not None:
            values.append(data[slot])
    return {'terms': {field: values}} if values else MATCH_NONE


def _facility_processing(params) -> List[dict]:
    fp = FacilityProcessingFilter.from_result(
        FacilityProcessingQuery(params).parse()
    )
    # Same structure as FacilityProcessingFilter.match_sql.
    clauses = []
    if fp.facility_types:
        clauses.append(_taxonomy_param(
            fp._classified_facility_types, 'facility_type', 2))
    if fp.legacy_processing_types:
        clauses.append(_taxonomy_param(
            fp._classified_processing_types, 'processing_type', 3))
    if fp.exact_processing_types:
        exact = {'terms': {'processing_type.lowercase': [
            value.lower() for value in fp.exact_processing_types
        ]}}
        if clauses and fp.legacy_processing_types:
            clauses[-1] = _any_of(clauses[-1], exact)
        else:
            clauses.append(exact)
    return clauses


def _isic(params) -> List[dict]:
    asked = {'4': params.getlist(P.ISIC_4), '5': params.getlist(P.ISIC_5)}
    columns = isic_filter_columns({
        revision: parse_isic_filter_values(values)
        for revision, values in asked.items()
    })
    if columns:
        return [_any_of(*({'terms': {column: codes}}
                          for column, codes in columns.items()))]
    if any(any(values) for values in asked.values()):
        # Legacy answers none(), never everything (OSDEV-3340 AC4).
        return [MATCH_NONE]
    return []


def _native_language_name(params) -> List[dict]:
    text = params.get(P.NATIVE_LANGUAGE_NAME)
    if not text:
        return []  # ILIKE '%%' matches every row
    if ARRAY_TEXT_SYNTAX.search(text):
        raise UnsupportedQuery('native_language_name: array syntax')
    return [_substring('native_language_name', text, folded=False)]


def _check_supported(params) -> None:
    if P.EMBED in params:
        raise UnsupportedQuery('embed')
    if params.getlist(P.PARTNER_CONTRIBUTOR):
        raise UnsupportedQuery('partner_contributor')


def build_filters(params) -> List[dict]:
    _check_supported(params)
    clauses = _id_and_text(params)
    clauses += _terms('country_code', params.getlist(P.COUNTRIES))
    clauses += _terms('contrib_types', params.getlist(P.CONTRIBUTOR_TYPES))
    clauses += _contributors(params)
    clauses += _terms('lists', params.getlist(P.LISTS))
    clauses += _boundary(params)
    clauses += _parent_company(params)
    clauses += _facility_processing(params)
    clauses += _isic(params)
    clauses += _terms('product_type',
                      [clean(v) for v in params.getlist(P.PRODUCT_TYPE)])
    clauses += _terms('number_of_workers',
                      params.getlist(P.NUMBER_OF_WORKERS))
    clauses += _native_language_name(params)
    clauses += _terms('sector', params.getlist(P.SECTOR))
    return clauses


def build_query(params) -> dict:
    return {'bool': {'filter': build_filters(params)}}


def build_sort(sort_by: Optional[str]) -> List[dict]:
    """The legacy order_by, with os_id standing in for id."""
    name = [{'name.sort': 'asc'}, {'name_tiebreak': 'asc'}]
    os_id = [{'os_id': 'asc'}]
    if sort_by in (None, 'contributors_desc'):
        return [{'contributors_count': 'desc'}, *name, *os_id]
    if sort_by == 'contributors_asc':
        return [{'contributors_count': 'asc'}, *name, *os_id]
    if sort_by == 'name_asc':
        return [*name, *os_id]
    if sort_by == 'name_desc':
        return [{'name.sort': 'desc'}, {'name_tiebreak': 'desc'}, *os_id]
    return os_id
