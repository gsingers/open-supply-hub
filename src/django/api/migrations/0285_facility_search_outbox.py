from django.db import migrations, models
from django.db.models import Q


# Every write to api_facilityindex gets a fresh search_version from the
# sequence, and every changed id lands once in the outbox. Statement-level
# triggers with transition tables keep a full index_facilities() pass to
# one INSERT ... SELECT per statement instead of one per row. Transition
# tables allow one event per trigger, hence three triggers sharing one
# function (each names its table changed_rows).
CREATE_TRIGGERS = """
CREATE SEQUENCE IF NOT EXISTS api_facilityindex_search_version_seq;

CREATE OR REPLACE FUNCTION api_facilityindex_set_search_version()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.search_version := nextval('api_facilityindex_search_version_seq');
    RETURN NEW;
END;
$$;

CREATE TRIGGER api_facilityindex_search_version
BEFORE INSERT OR UPDATE ON api_facilityindex
FOR EACH ROW EXECUTE FUNCTION api_facilityindex_set_search_version();

CREATE OR REPLACE FUNCTION api_facilityindex_enqueue_search_sync()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    INSERT INTO api_facilitysearchoutbox (facility_id, created_at)
    SELECT DISTINCT changed_rows.id, now()
    FROM changed_rows
    ON CONFLICT (facility_id) WHERE claimed_at IS NULL DO NOTHING;
    RETURN NULL;
END;
$$;

CREATE TRIGGER api_facilityindex_search_sync_insert
AFTER INSERT ON api_facilityindex
REFERENCING NEW TABLE AS changed_rows
FOR EACH STATEMENT EXECUTE FUNCTION api_facilityindex_enqueue_search_sync();

CREATE TRIGGER api_facilityindex_search_sync_update
AFTER UPDATE ON api_facilityindex
REFERENCING NEW TABLE AS changed_rows
FOR EACH STATEMENT EXECUTE FUNCTION api_facilityindex_enqueue_search_sync();

CREATE TRIGGER api_facilityindex_search_sync_delete
AFTER DELETE ON api_facilityindex
REFERENCING OLD TABLE AS changed_rows
FOR EACH STATEMENT EXECUTE FUNCTION api_facilityindex_enqueue_search_sync();
"""

DROP_TRIGGERS = """
DROP TRIGGER IF EXISTS api_facilityindex_search_sync_delete
    ON api_facilityindex;
DROP TRIGGER IF EXISTS api_facilityindex_search_sync_update
    ON api_facilityindex;
DROP TRIGGER IF EXISTS api_facilityindex_search_sync_insert
    ON api_facilityindex;
DROP FUNCTION IF EXISTS api_facilityindex_enqueue_search_sync();
DROP TRIGGER IF EXISTS api_facilityindex_search_version
    ON api_facilityindex;
DROP FUNCTION IF EXISTS api_facilityindex_set_search_version();
DROP SEQUENCE IF EXISTS api_facilityindex_search_version_seq;
"""


class Migration(migrations.Migration):
    """
    Change capture for the `facilities` OpenSearch index that backs /map:
    an outbox of changed facility ids, filled by triggers on
    api_facilityindex, and a per-row search_version used as the external
    document version. Existing rows keep a NULL search_version until they
    are next written; the full rebuild command treats NULL as version 0.
    """

    dependencies = [
        ('api', '0284_zone_set_last_upload_sha256'),
    ]

    operations = [
        migrations.CreateModel(
            name='FacilitySearchOutbox',
            fields=[
                ('id', models.BigAutoField(primary_key=True, serialize=False)),
                ('facility_id', models.CharField(
                    help_text=(
                        'The OS ID whose search document must be '
                        'refreshed.'
                    ),
                    max_length=32,
                )),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('claimed_at', models.DateTimeField(
                    blank=True,
                    help_text=(
                        'When an indexer claimed the entry. A claim older '
                        'than the indexer lease is taken again, so a '
                        'crashed indexer loses nothing.'
                    ),
                    null=True,
                )),
            ],
            options={
                'indexes': [models.Index(
                    fields=['claimed_at', 'id'],
                    name='api_fso_claim_idx',
                )],
                'constraints': [models.UniqueConstraint(
                    condition=Q(claimed_at__isnull=True),
                    fields=('facility_id',),
                    name='api_facilitysearchoutbox_one_pending',
                )],
            },
        ),
        migrations.AddField(
            model_name='facilityindex',
            name='search_version',
            field=models.BigIntegerField(
                editable=False,
                help_text=(
                    'Set from a sequence by a BEFORE INSERT OR UPDATE '
                    'trigger (migration 0285) on every write, whatever '
                    'path made it. The facilities OpenSearch index uses '
                    'it as the external document version, so a stale '
                    'write (from a rebuild racing the indexer) never '
                    'overwrites a newer one.'
                ),
                null=True,
            ),
        ),
        migrations.RunSQL(CREATE_TRIGGERS, DROP_TRIGGERS),
    ]
