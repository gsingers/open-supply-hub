"""
Compare /api/facilities/ results from Postgres and from the facilities
index, query by query, and summarise the mismatches per parameter.
"""
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from typing import Iterable, List, Optional
from urllib.parse import urlsplit

from django.contrib.gis.db.models import Extent
from django.http import QueryDict

from api.facility_search.legacy import legacy_search_queryset
from api.facility_search.query import UnsupportedQuery
from api.facility_search.search import (
    MAX_RESULT_WINDOW,
    execute,
    plan_search,
)
from api.facility_search.shadow import extents_match

PAGE_PARAMS = {'page', 'pageSize'}


@dataclass
class QueryComparison:
    query: str
    params: List[str]
    routed: Optional[str] = None
    error: Optional[str] = None
    postgres_count: Optional[int] = None
    opensearch_count: Optional[int] = None
    count_match: Optional[bool] = None
    ids_match: Optional[bool] = None
    id_overlap: Optional[float] = None
    extent_match: Optional[bool] = None

    @property
    def mismatched(self):
        return False in (self.count_match, self.ids_match, self.extent_match)


@dataclass
class ParamSummary:
    queries: int = 0
    routed: int = 0
    errors: int = 0
    count_mismatches: int = 0
    id_mismatches: int = 0
    extent_mismatches: int = 0


@dataclass
class ParityReport:
    comparisons: List[QueryComparison] = field(default_factory=list)

    def by_param(self):
        summary = defaultdict(ParamSummary)
        for comparison in self.comparisons:
            for name in ['(all)'] + (comparison.params or ['(none)']):
                row = summary[name]
                row.queries += 1
                row.routed += comparison.routed is not None
                row.errors += comparison.error is not None
                row.count_mismatches += comparison.count_match is False
                row.id_mismatches += comparison.ids_match is False
                row.extent_mismatches += comparison.extent_match is False
        return dict(sorted(summary.items()))

    def as_dict(self):
        return {
            'by_param': {k: asdict(v) for k, v in self.by_param().items()},
            'mismatches': [asdict(c) for c in self.comparisons
                           if c.mismatched or c.error],
            'routed': [asdict(c) for c in self.comparisons if c.routed],
        }


def parse_query(line: str) -> Optional[str]:
    """Accept a bare query string, '?q=...', or a URL or path."""
    line = line.strip()
    if not line or line.startswith('#'):
        return None
    if '?' in line or line.startswith(('/', 'http')):
        return urlsplit(line).query
    return line


def _window(params, top: int):
    try:
        page = max(int(params.get('page', 1)), 1)
    except ValueError:
        page = 1
    return (page - 1) * top, top


def compare_query(client, query: str, top: int = 50,
                  timeout: float = 30) -> QueryComparison:
    params = QueryDict(query)
    comparison = QueryComparison(
        query=query,
        params=sorted(set(params) - PAGE_PARAMS),
    )
    sort_by = params.get('sort_by')
    offset, size = _window(params, top)
    if offset + size > MAX_RESULT_WINDOW:
        comparison.routed = 'page past the result window'
        return comparison
    try:
        plan = plan_search(params, sort_by, offset, size)
    except UnsupportedQuery as reason:
        comparison.routed = str(reason)
        return comparison

    queryset = legacy_search_queryset(params, sort_by)
    legacy_ids = list(queryset.values_list('id', flat=True)
                      [offset:offset + size])
    comparison.postgres_count = queryset.count()
    legacy_extent = queryset.aggregate(Extent('location'))[
        'location__extent']

    try:
        result = execute(client, plan, timeout)
    except Exception as error:
        comparison.error = str(error)
        return comparison
    comparison.opensearch_count = result.total
    comparison.count_match = result.total == comparison.postgres_count
    comparison.ids_match = result.ids == legacy_ids
    union = set(result.ids) | set(legacy_ids)
    comparison.id_overlap = (
        len(set(result.ids) & set(legacy_ids)) / len(union) if union else 1.0
    )
    comparison.extent_match = extents_match(result.extent, legacy_extent)
    return comparison


def run(client, lines: Iterable[str], top: int = 50,
        timeout: float = 30) -> ParityReport:
    report = ParityReport()
    for line in lines:
        query = parse_query(line)
        if query is not None:
            report.comparisons.append(
                compare_query(client, query, top, timeout))
    return report
