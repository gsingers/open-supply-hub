from django.db import models
from django.db.models import Q


class FacilitySearchOutbox(models.Model):
    """
    Facilities whose `api_facilityindex` row changed and whose document in
    the `facilities` OpenSearch index must be refreshed.

    Written only by the statement triggers on `api_facilityindex`
    (migration 0285), so every path that maintains the index
    (`index_facilities_by`, `index_facilities()`, the backfills) is
    captured. At most one unclaimed row exists per facility: the trigger
    inserts with `ON CONFLICT DO NOTHING` against the partial unique index.

    The row records only *which* facility changed. The indexer reads the
    current `api_facilityindex` row when it processes the entry, upserting
    the document if the row exists and deleting it otherwise, so replays
    and duplicates are harmless.
    """
    id = models.BigAutoField(primary_key=True)
    facility_id = models.CharField(
        max_length=32,
        help_text='The OS ID whose search document must be refreshed.',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    claimed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            'When an indexer claimed the entry. A claim older than the '
            'indexer lease is taken again, so a crashed indexer loses '
            'nothing.'
        ),
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['facility_id'],
                condition=Q(claimed_at__isnull=True),
                name='api_facilitysearchoutbox_one_pending',
            ),
        ]
        indexes = [
            models.Index(
                fields=['claimed_at', 'id'],
                name='api_fso_claim_idx',
            ),
        ]
