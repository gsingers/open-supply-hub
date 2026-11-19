"""Claim and settle `api_facilitysearchoutbox` entries."""
import logging
from typing import List, NamedTuple

from django.db import IntegrityError, connection, transaction

log = logging.getLogger(__name__)

CLAIM_SQL = """
UPDATE api_facilitysearchoutbox
SET claimed_at = now()
WHERE id IN (
    SELECT id
    FROM api_facilitysearchoutbox
    WHERE claimed_at IS NULL
       OR claimed_at < now() - make_interval(secs => %(lease)s)
    ORDER BY id
    LIMIT %(limit)s
    FOR UPDATE SKIP LOCKED
)
RETURNING id, facility_id
"""

# A released entry would collide with a newer pending entry for the same
# facility; that newer entry already covers it, so drop the old one.
DROP_SUPERSEDED_SQL = """
DELETE FROM api_facilitysearchoutbox claimed
WHERE claimed.id = ANY(%(ids)s)
  AND EXISTS (
      SELECT 1 FROM api_facilitysearchoutbox pending
      WHERE pending.facility_id = claimed.facility_id
        AND pending.claimed_at IS NULL
  )
"""

RELEASE_SQL = """
UPDATE api_facilitysearchoutbox
SET claimed_at = NULL
WHERE id = ANY(%(ids)s)
"""


class OutboxEntry(NamedTuple):
    id: int
    facility_id: str


def claim(limit: int, lease_seconds: float) -> List[OutboxEntry]:
    """
    Claim up to `limit` entries, oldest first, in a short transaction of
    its own so writers are never blocked behind an OpenSearch call. A claim
    older than `lease_seconds` is taken over: its indexer died.
    """
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(CLAIM_SQL, {'lease': lease_seconds, 'limit': limit})
        return sorted(
            (OutboxEntry(*row) for row in cursor.fetchall()),
            key=lambda entry: entry.id,
        )


def complete(entry_ids: List[int]) -> None:
    if not entry_ids:
        return
    with connection.cursor() as cursor:
        cursor.execute(
            'DELETE FROM api_facilitysearchoutbox WHERE id = ANY(%s)',
            [entry_ids],
        )


def release(entry_ids: List[int]) -> None:
    """
    Give entries back for an immediate retry. If a newer pending entry
    races in, leave them claimed; the lease brings them back later.
    """
    if not entry_ids:
        return
    try:
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(DROP_SUPERSEDED_SQL, {'ids': entry_ids})
            cursor.execute(RELEASE_SQL, {'ids': entry_ids})
    except IntegrityError:
        log.info(
            '[Facility search indexer] Could not release %s entries; '
            'they will be retried when their lease expires.',
            len(entry_ids),
        )
