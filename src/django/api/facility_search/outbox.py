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
    WHERE parked_at IS NULL
      AND (claimed_at IS NULL
           OR claimed_at < now() - make_interval(secs => %(lease)s))
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


# Count one rejected attempt; park the entry once it reaches the limit,
# otherwise give it back for the next batch.
FAIL_SQL = """
UPDATE api_facilitysearchoutbox
SET attempts = attempts + 1,
    last_error = %(error)s,
    parked_at = CASE WHEN attempts + 1 >= %(max_attempts)s
                     THEN now() ELSE NULL END
WHERE id = %(id)s
RETURNING parked_at IS NOT NULL
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


def fail(entries_with_errors, max_attempts: int) -> int:
    """
    Record a rejected attempt for each (entry id, error) pair, park the
    ones at the limit and release the rest. Returns how many were parked.
    """
    parked, retry = 0, []
    with connection.cursor() as cursor:
        for entry_id, error in entries_with_errors:
            cursor.execute(FAIL_SQL, {'id': entry_id, 'error': str(error),
                                      'max_attempts': max_attempts})
            if cursor.fetchone()[0]:
                parked += 1
            else:
                retry.append(entry_id)
    release(retry)
    return parked


def enqueue(facility_ids) -> None:
    """Ask the indexer to re-settle these facilities from their rows."""
    if not facility_ids:
        return
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO api_facilitysearchoutbox
                (facility_id, created_at, attempts, last_error)
            SELECT unnest(%s::varchar[]), now(), 0, ''
            ON CONFLICT (facility_id) WHERE claimed_at IS NULL DO NOTHING
            """,
            [list(facility_ids)],
        )
