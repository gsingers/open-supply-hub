"""
Build a new `facilities-v<N>` index from `api_facilityindex` and swap the
`facilities` alias to it with no downtime.

1. Create the index with refresh off and no replicas (fast bulk load).
2. Mark it with the `facilities-building` alias. From here the outbox
   indexer writes every change to it as well as to the live index.
3. Stream every indexable row into it. Documents carry `search_version`
   as an external version, so a row read before a concurrent change can
   never overwrite the indexer's newer copy.
4. Restore refresh and replicas, delete documents whose rows are gone
   (a delete applied before the stale snapshot copy arrived), and drain
   the outbox.
5. Atomically point `facilities` at the new index and drop the building
   alias. The previous index is kept for rollback unless asked otherwise.

Use it for the initial load and for every mapping change. It is also the
cheap way to resync after a full `index_facilities()` pass.
"""
import logging
from typing import Callable, Iterable, List, Optional

from opensearchpy.helpers import scan

from api.facility_search import documents
from api.facility_search.index_definition import (
    building_alias,
    concrete_index_prefix,
    read_alias,
    concrete_index_name,
    index_body,
    parse_version,
)
from api.facility_search import outbox
from api.facility_search.indexer import (
    FacilitySearchIndexer,
    SETTLED_STATUSES,
    failed_ids,
)

log = logging.getLogger(__name__)


class RebuildError(Exception):
    pass


def alias_indices(client, alias: str) -> List[str]:
    response = client.indices.get_alias(index=f'{concrete_index_prefix()}*')
    return sorted(index for index, data in response.items()
                  if alias in data.get('aliases', {}))


def next_version(client) -> int:
    existing = client.indices.get(index=f'{concrete_index_prefix()}*',
                                  ignore_unavailable=True,
                                  allow_no_indices=True)
    versions = [parse_version(name) for name in existing]
    return max([v for v in versions if v is not None], default=0) + 1


def swap_alias(client, new_index: str) -> List[str]:
    """Point the read alias at `new_index` in one atomic request."""
    old = [i for i in alias_indices(client, read_alias()) if i != new_index]
    actions = [{'add': {'index': new_index, 'alias': read_alias()}}]
    actions += [{'remove': {'index': index, 'alias': read_alias()}}
                for index in old]
    if new_index in alias_indices(client, building_alias()):
        actions.append({'remove': {'index': new_index,
                                   'alias': building_alias()}})
    client.indices.update_aliases(body={'actions': actions})
    return old


def _bulk_load(client, index: str, rows: Iterable[dict], batch_size: int,
               timeout: float) -> int:
    count = 0
    actions = []
    for row in rows:
        actions.append({'index': {
            '_index': index, '_id': row['id'],
            'version': documents.document_version(row),
            'version_type': 'external_gte',
        }})
        actions.append(documents.build_document(row))
        if len(actions) >= 2 * batch_size:
            count += _send(client, actions, timeout)
            actions = []
    if actions:
        count += _send(client, actions, timeout)
    return count


def _send(client, actions: list, timeout: float) -> int:
    response = client.bulk(body=actions, request_timeout=timeout)
    if response.get('errors'):
        errors = [item['index'] for item in response['items']
                  if item['index'].get('status', 500) >= 300
                  and item['index'].get('status') not in SETTLED_STATUSES]
        if errors:
            raise RebuildError(f'{len(errors)} documents failed, first: '
                               f'{errors[0]}')
    return len(actions) // 2


def delete_orphans(client, index: str, timeout: float) -> int:
    """
    Delete documents whose api_facilityindex row no longer exists.

    The indexer keeps writing to the index while this runs, so a document
    can appear after the live ids were read. Candidates are therefore
    re-checked against Postgres just before the delete, and the delete
    carries the sequence value read before that check: a document written
    from a row versioned after it rejects the delete (409). Every deleted
    id is also put back on the outbox, so the indexer settles whatever is
    left of the race from the row's state.
    """
    version = documents.delete_version()
    live_ids = set(documents.indexable_rows().values_list('id', flat=True))
    candidates = {hit['_id'] for hit in scan(client, index=index,
                                             query={'_source': False})
                  if hit['_id'] not in live_ids}
    if not candidates:
        return 0
    orphans = sorted(candidates - set(
        documents.indexable_rows().filter(id__in=candidates)
        .values_list('id', flat=True)
    ))
    if not orphans:
        return 0
    response = client.bulk(body=[{'delete': {
        '_index': index, '_id': os_id, 'version': version,
        'version_type': 'external_gte',
    }} for os_id in orphans], request_timeout=timeout)
    failed = failed_ids(response) if response.get('errors') else {}
    if failed:
        raise RebuildError(f'{len(failed)} orphan deletes failed, first: '
                           f'{next(iter(failed.items()))}')
    outbox.enqueue(orphans)
    return len(orphans)


def _load(client, index, batch_size, replicas, timeout, report):
    rows = documents.indexable_rows().order_by().iterator(
        chunk_size=batch_size)
    loaded = _bulk_load(client, index, rows, batch_size, timeout)
    report(f'Loaded {loaded} documents.')

    client.indices.put_settings(index=index, body={'index': {
        'refresh_interval': None, 'number_of_replicas': replicas,
    }})
    client.indices.refresh(index=index)
    report(f'Deleted {delete_orphans(client, index, timeout)} orphans.')
    client.indices.refresh(index=index)


def rebuild(client, version: Optional[int] = None, batch_size: int = 2000,
            replicas: int = 1, swap: bool = True, delete_old: bool = False,
            timeout: float = 120,
            report: Callable[[str], None] = log.info) -> str:
    index = concrete_index_name(version or next_version(client))
    body = index_body(number_of_replicas=0)
    body['settings']['refresh_interval'] = '-1'
    client.indices.create(index=index, body=body)
    client.indices.put_alias(index=index, name=building_alias())
    report(f'Created {index}; loading api_facilityindex.')
    try:
        _load(client, index, batch_size, replicas, timeout, report)
        FacilitySearchIndexer(client).run(once=True)
        old = swap_alias(client, index) if swap else None
    except Exception:
        # An abandoned build must not keep receiving (and possibly
        # failing) the indexer's writes. The index is kept to inspect.
        client.indices.delete_alias(index=index, name=building_alias(),
                                    ignore=[404])
        report(f'Build of {index} failed; it no longer receives changes.')
        raise

    if not swap:
        report(f'{index} is ready; the read alias was not moved.')
        return index
    report(f'{read_alias()} now points at {index} (was {old or "nothing"}).')
    if delete_old:
        for old_index in old:
            client.indices.delete(index=old_index)
            report(f'Deleted {old_index}.')
    return index
