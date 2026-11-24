"""
The OpenSearch path of `FacilitiesViewSet.list` and its shadow mode.

The view keeps everything that shapes the response (serializer, masking,
visibility, response cache, pagination links). This module only produces
the page of FacilityIndex rows, the total, the extent and the
anonymised-only flag, or None to make the view use Postgres.
"""
import logging
from dataclasses import dataclass
from typing import List, Optional

from api.facility_search import backend, outbox, shadow
from api.facility_search.query import UnsupportedQuery
from api.facility_search.search import (
    count_documents,
    MAX_RESULT_WINDOW,
    Extent,
    execute,
    hydrate,
    plan_search,
)

log = logging.getLogger(__name__)


class PrefetchedResults:
    """
    A stand-in for a queryset that lets the stock paginator build the page
    and its links from a total and the one page already fetched.
    """

    def __init__(self, total: int, offset: int, rows: list):
        self.total = total
        self.offset = offset
        self.rows = rows

    def count(self) -> int:
        return self.total

    def __len__(self) -> int:
        return self.total

    def __getitem__(self, key):
        if not isinstance(key, slice) or (key.start or 0) != self.offset:
            raise IndexError('only the prefetched page is available')
        return self.rows


@dataclass
class OpenSearchPage:
    rows: List
    extent: Optional[Extent]
    anonymised_only: bool
    highlights: dict


def _page_window(view, request):
    try:
        page_number = int(request.query_params.get('page', 1))
    except (TypeError, ValueError):
        return None  # 'last' or garbage: Postgres answers as before
    size = view.paginator.get_page_size(request)
    if page_number < 1 or not size:
        return None
    offset = (page_number - 1) * size
    if offset + size > MAX_RESULT_WINDOW:
        return None
    return offset, size


def opensearch_list_page(view, request, sort_by, flagged):
    window = _page_window(view, request)
    if window is None:
        return None
    offset, size = window
    try:
        plan = plan_search(request.query_params, sort_by, offset, size,
                           flagged.contributor_ids)
    except UnsupportedQuery as reason:
        log.info('[Map OpenSearch] Postgres serves this search: %s', reason)
        return None
    try:
        result = execute(backend.client(), plan, backend.request_timeout())
    except Exception as error:
        backend.breaker.record_failure()
        log.warning('[Map OpenSearch] Falling back to Postgres: %s', error)
        return None
    backend.breaker.record_success()

    detail = request.query_params.get('detail', '').lower() == 'true'
    rows = hydrate(result.ids, detail=detail)
    if len(rows) < len(result.ids):
        # The index is behind Postgres for these ids, so its total and
        # page are too: serve this request from Postgres and have the
        # indexer settle the stale documents.
        stale = set(result.ids) - {row.id for row in rows}
        log.warning('[Map OpenSearch] %s stale documents; Postgres serves '
                    'this search', len(stale))
        outbox.enqueue(sorted(stale))
        return None
    page = view.paginate_queryset(
        PrefetchedResults(result.total, offset, rows)
    )
    anonymised_only = (bool(flagged.contributor_ids)
                       and not result.has_downloadable
                       and result.total > 0)
    return OpenSearchPage(page, result.extent, anonymised_only,
                          result.highlights)


def shadow_list_search(view, request, sort_by, flagged, page_rows, total,
                       extent, postgres_seconds):
    """Queue an OpenSearch run of the search Postgres just served."""
    window = _page_window(view, request)
    # Postgres has no relevance order to compare a scored search with.
    if sort_by == 'relevance':
        return None
    if window is None or not shadow.sampled():
        return None
    offset, size = window
    try:
        plan = plan_search(request.query_params, sort_by, offset, size,
                           flagged.contributor_ids)
    except UnsupportedQuery:
        return None
    legacy = shadow.LegacyResult(
        total=total, ids=[row.id for row in page_rows], extent=extent,
        seconds=postgres_seconds or 0.0,
    )
    return shadow.submit(plan, legacy, request.query_params.urlencode())


def opensearch_facility_count():
    """Documents in the index (one per indexed facility), or None."""
    try:
        count = count_documents(backend.client(), backend.request_timeout())
    except Exception as error:
        backend.breaker.record_failure()
        log.warning('[Map OpenSearch] Count falling back to Postgres: %s',
                    error)
        return None
    backend.breaker.record_success()
    return count
