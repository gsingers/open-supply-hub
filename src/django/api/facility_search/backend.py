"""
Which backend serves /map, and the safety rails around OpenSearch.

Waffle switches, all created inactive (migration 0286), so Postgres stays
the default and any of them can be flipped at runtime without a deploy:

* `map_opensearch_search` serves `/api/facilities/` (list, extent) and
  `/api/facilities/count/` from OpenSearch.
* `map_opensearch_tiles` serves `facilitygrid` tiles from OpenSearch.
* `map_opensearch_shadow` keeps serving Postgres but also runs the
  OpenSearch query for a sample of requests and logs latency and diffs.

Any OpenSearch error or timeout falls back to Postgres. After repeated
failures a per-process circuit breaker skips OpenSearch for a cooldown.
"""
import logging
import threading
import time

from django.conf import settings
from waffle import switch_is_active

log = logging.getLogger(__name__)

SEARCH_SWITCH = 'map_opensearch_search'
TILES_SWITCH = 'map_opensearch_tiles'
SHADOW_SWITCH = 'map_opensearch_shadow'


class CircuitBreaker:
    def __init__(self, threshold=None, cooldown_seconds=None,
                 clock=time.monotonic):
        self._threshold = threshold
        self._cooldown = cooldown_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._failures = 0
        self._open_until = 0.0

    @property
    def threshold(self):
        return (self._threshold if self._threshold is not None
                else settings.MAP_OPENSEARCH_BREAKER_THRESHOLD)

    @property
    def cooldown(self):
        return (self._cooldown if self._cooldown is not None
                else settings.MAP_OPENSEARCH_BREAKER_COOLDOWN_SECONDS)

    def allow(self) -> bool:
        with self._lock:
            return self._clock() >= self._open_until

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._failures >= self.threshold:
                self._open_until = self._clock() + self.cooldown
                self._failures = 0
                log.error('[Map OpenSearch] Circuit open for %ss',
                          self.cooldown)


breaker = CircuitBreaker()


def search_enabled() -> bool:
    return switch_is_active(SEARCH_SWITCH) and breaker.allow()


def tiles_enabled() -> bool:
    return switch_is_active(TILES_SWITCH) and breaker.allow()


def shadow_enabled() -> bool:
    return switch_is_active(SHADOW_SWITCH) and breaker.allow()


def client():
    from api.services.opensearch.opensearch import (
        OpenSearchServiceConnection,
    )
    return OpenSearchServiceConnection().map_client


def request_timeout() -> float:
    return settings.MAP_OPENSEARCH_REQUEST_TIMEOUT_SECONDS
