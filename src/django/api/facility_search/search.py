"""
Run a /map search against the `facilities` index.

One request returns the page of ids, the exact total, the extent of the
whole result set and, when some contributors are anonymised, whether any
result has another contributor (the `anonymised_only` flag). The page is
then hydrated from Postgres by primary key so the response is serialized
by the unchanged FacilityIndexSerializer.
"""
from dataclasses import dataclass
from typing import Iterable, List, Optional, Tuple

from api.facility_search.index_definition import read_alias
from api.facility_search.query import build_query, build_sort
from api.models.facility.facility_index import FacilityIndex

# index.max_result_window: deeper pages stay on Postgres.
MAX_RESULT_WINDOW = 10000

Extent = Tuple[float, float, float, float]


@dataclass
class SearchPlan:
    """A query built from request params, ready to run."""
    body: dict


@dataclass
class SearchResult:
    total: int
    ids: List[str]
    extent: Optional[Extent]
    has_downloadable: Optional[bool]


def plan_search(params, sort_by: Optional[str], offset: int, size: int,
                anonymised_ids: Iterable[int] = ()) -> SearchPlan:
    """Build the request body. Raises UnsupportedQuery."""
    aggs = {'extent': {'geo_bounds': {'field': 'location',
                                      'wrap_longitude': False}}}
    anonymised_ids = sorted(anonymised_ids)
    if anonymised_ids:
        # Any bucket left after excluding the anonymised contributors is a
        # result with a real contributor, i.e. a downloadable row.
        aggs['downloadable'] = {'terms': {
            'field': 'contributors_id', 'exclude': anonymised_ids, 'size': 1,
        }}
    return SearchPlan(body={
        'query': build_query(params),
        'sort': build_sort(sort_by),
        'from': offset,
        'size': size,
        '_source': False,
        'track_total_hits': True,
        'aggs': aggs,
    })


def parse_extent(aggregations: dict) -> Optional[Extent]:
    bounds = aggregations.get('extent', {}).get('bounds')
    if not bounds:
        return None
    top_left, bottom_right = bounds['top_left'], bounds['bottom_right']
    # Same order as PostGIS Extent: (xmin, ymin, xmax, ymax).
    return (top_left['lon'], bottom_right['lat'],
            bottom_right['lon'], top_left['lat'])


def execute(client, plan: SearchPlan, timeout: float) -> SearchResult:
    response = client.search(index=read_alias(), body=plan.body,
                             request_timeout=timeout)
    aggregations = response.get('aggregations', {})
    downloadable = aggregations.get('downloadable')
    return SearchResult(
        total=response['hits']['total']['value'],
        ids=[hit['_id'] for hit in response['hits']['hits']],
        extent=parse_extent(aggregations),
        has_downloadable=(None if downloadable is None
                          else bool(downloadable['buckets'])),
    )


def hydrate(ids: List[str]) -> List[FacilityIndex]:
    """
    The FacilityIndex rows for `ids`, in the same order. An id with no row
    (deleted since it was indexed) is dropped.
    """
    rows = FacilityIndex.objects.in_bulk(ids)
    return [rows[os_id] for os_id in ids if os_id in rows]


def count_documents(client, timeout: float) -> int:
    return client.count(index=read_alias(), request_timeout=timeout)['count']
