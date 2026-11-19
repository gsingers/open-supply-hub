"""
Shadow mode: Postgres serves the request; OpenSearch runs the same search
in a background thread and the two results are compared and logged.
"""
import json
import logging
import random
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import List, Optional

from django.conf import settings

from api.facility_search import backend
from api.facility_search.search import Extent, SearchPlan, execute

log = logging.getLogger(__name__)

# Small and bounded: shadow work must never queue up behind real traffic.
_executor = ThreadPoolExecutor(max_workers=2,
                               thread_name_prefix='map-shadow')

EXTENT_TOLERANCE = 1e-5  # geo_point doc values are lossy below ~1e-7 deg


@dataclass
class LegacyResult:
    total: int
    ids: List[str]
    extent: Optional[Extent]
    seconds: float


def extents_match(a: Optional[Extent], b: Optional[Extent]) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return all(abs(x - y) <= EXTENT_TOLERANCE for x, y in zip(a, b))


def compare(plan: SearchPlan, legacy: LegacyResult, query_string: str,
            client=None) -> dict:
    started = time.monotonic()
    try:
        result = execute(client or backend.client(), plan,
                         backend.request_timeout())
    except Exception as error:
        backend.breaker.record_failure()
        report = {'query': query_string, 'error': str(error)}
        log.warning('[Map OpenSearch shadow] %s', json.dumps(report))
        return report
    backend.breaker.record_success()
    report = {
        'query': query_string,
        'postgres_ms': round(legacy.seconds * 1000, 1),
        'opensearch_ms': round((time.monotonic() - started) * 1000, 1),
        'count_match': result.total == legacy.total,
        'postgres_count': legacy.total,
        'opensearch_count': result.total,
        'ids_match': result.ids == legacy.ids,
        'extent_match': extents_match(result.extent, legacy.extent),
    }
    log.info('[Map OpenSearch shadow] %s', json.dumps(report))
    return report


def sampled() -> bool:
    return random.random() < settings.MAP_OPENSEARCH_SHADOW_SAMPLE_RATE


def submit(plan: SearchPlan, legacy: LegacyResult, query_string: str):
    return _executor.submit(compare, plan, legacy, query_string)
