"""
Apply `api_facilitysearchoutbox` entries to the `facilities` index.

Each batch claims entries, reads the current `api_facilityindex` rows for
their facilities, and sends one bulk request: an upsert for every row
that exists, a delete for every facility that has none. Documents carry
the row's `search_version` as an external version, so replays and races
with a rebuild cannot regress a document. Entries are deleted only after
the bulk request succeeded for their facility.
"""
import logging
import time
from typing import Callable, Dict, Iterable, List

from api.facility_search import documents, outbox
from api.facility_search.index_definition import (
    building_alias,
    concrete_index_prefix,
    read_alias,
)

log = logging.getLogger(__name__)

# A stale write (409) means a newer version is already indexed; a missing
# document on delete (404) is the state the delete wanted.
SETTLED_STATUSES = {409}
SETTLED_DELETE_STATUSES = {404, 409}


def write_targets(client) -> List[str]:
    """Concrete indices behind the read alias and any index being built."""
    response = client.indices.get_alias(index=f'{concrete_index_prefix()}*')
    return sorted(
        index
        for index, data in response.items()
        if {read_alias(), building_alias()} & set(data.get('aliases', {}))
    )


def bulk_actions(facility_ids: Iterable[str], rows: Dict[str, dict],
                 targets: Iterable[str], delete_version: int) -> List[dict]:
    actions = []
    for target in targets:
        for facility_id in facility_ids:
            row = rows.get(facility_id)
            meta = {'_index': target, '_id': facility_id,
                    'version_type': 'external_gte'}
            if row is None:
                actions.append({'delete': {**meta,
                                           'version': delete_version}})
            else:
                actions.append({'index': {
                    **meta, 'version': documents.document_version(row),
                }})
                actions.append(documents.build_document(row))
    return actions


def failed_ids(bulk_response: dict) -> set:
    failed = set()
    for item in bulk_response.get('items', []):
        (operation, result), = item.items()
        status = result.get('status', 500)
        settled = (SETTLED_DELETE_STATUSES if operation == 'delete'
                   else SETTLED_STATUSES)
        if status >= 300 and status not in settled:
            failed.add(result.get('_id'))
            log.error('[Facility search indexer] %s %s failed: %s',
                      operation, result.get('_id'), result.get('error'))
    return failed


class FacilitySearchIndexer:
    def __init__(self, client, batch_size: int = 500,
                 lease_seconds: float = 300,
                 request_timeout: float = 30,
                 sleep: Callable[[float], None] = time.sleep):
        self.client = client
        self.batch_size = batch_size
        self.lease_seconds = lease_seconds
        self.request_timeout = request_timeout
        self.sleep = sleep

    def process_batch(self) -> int:
        """Handle one batch; return how many entries were claimed."""
        entries = outbox.claim(self.batch_size, self.lease_seconds)
        if not entries:
            return 0
        try:
            failed = self._apply({entry.facility_id for entry in entries})
        except Exception:
            outbox.release([entry.id for entry in entries])
            raise
        outbox.complete([e.id for e in entries if e.facility_id not in failed])
        outbox.release([e.id for e in entries if e.facility_id in failed])
        if failed:
            raise IndexingError(f'{len(failed)} documents failed to index')
        return len(entries)

    def _apply(self, facility_ids: set) -> set:
        # Targets are resolved after the claim: an entry committed before a
        # rebuild marked its index is in that rebuild's snapshot.
        targets = write_targets(self.client)
        if not targets:
            log.info('[Facility search indexer] No facilities index yet; '
                     'dropping %s entries (a rebuild loads everything).',
                     len(facility_ids))
            return set()
        ordered_ids = sorted(facility_ids)
        actions = bulk_actions(ordered_ids, documents.fetch_rows(ordered_ids),
                               targets, documents.delete_version())
        response = self.client.bulk(body=actions,
                                    request_timeout=self.request_timeout)
        return failed_ids(response) if response.get('errors') else set()

    def run(self, once: bool = False, idle_seconds: float = 2,
            max_backoff_seconds: float = 60) -> None:
        backoff = 1.0
        while True:
            try:
                claimed = self.process_batch()
                backoff = 1.0
            except Exception as error:
                log.exception('[Facility search indexer] Batch failed: %s',
                              error)
                if once:
                    raise
                self.sleep(backoff)
                backoff = min(backoff * 2, max_backoff_seconds)
                continue
            if once and claimed < self.batch_size:
                return
            if claimed == 0:
                self.sleep(idle_seconds)


class IndexingError(Exception):
    pass
