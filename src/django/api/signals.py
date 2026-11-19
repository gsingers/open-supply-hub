import logging
import json

from django.conf import settings
from django.db import transaction
from django.db.models.signals import post_delete, post_save, pre_delete
from django.dispatch import receiver
from opensearchpy.exceptions import NotFoundError, OpenSearchException

from api.models.facility.facility import Facility
from api.models.facility.facility_list_item import FacilityListItem
from api.models.extended_field import ExtendedField
from api.models.facility.facility_claim import FacilityClaim
from api.models.facility.facility_claim_attachments import (
    FacilityClaimAttachments
)
from api.models.facility.facility_activity_report import FacilityActivityReport
from api.models.facility.facility_location import FacilityLocation
from api.models.facility.facility_alias import FacilityAlias
from api.models.moderation_event import ModerationEvent
from api.models.contributor.contributor import Contributor
from api.services.opensearch.opensearch import OpenSearchServiceConnection
from oar.rollbar import report_error_to_rollbar
from api.views.v1.index_names import OpenSearchIndexNames


log = logging.getLogger(__name__)


def signal_error_notifier(error_log_message, response):
    log.error(error_log_message)
    report_error_to_rollbar(
        message=error_log_message,
        extra_data={
            'response_result': json.dumps(response),
        }
    )


@receiver(post_delete, sender=Facility)
def location_post_delete_handler_for_opensearch(instance, **kwargs):
    """
    Remove the facility's production-locations document once the delete
    commits.

    Runs on ``transaction.on_commit`` so a rolled-back delete never removes
    a live document, and never raises: Postgres is the source of truth and
    an OpenSearch outage must not fail or roll back the write. Logstash
    cannot observe deletes, so a failed delete is reported to Rollbar; the
    map search path drops ids it cannot hydrate from Postgres, so an
    orphaned document is never served as a result.

    Candidates (OSDEV-3243) are kept out of the index, so for them the
    document is normally missing. The delete is still attempted so that a
    document left behind by a confirmed-to-candidate transition can never
    be orphaned; a 404 or an outage is then expected and not reported.
    """
    facility_id = instance.id
    is_candidate = instance.is_candidate
    transaction.on_commit(
        lambda: _delete_location_document(facility_id, is_candidate)
    )


def _delete_location_document(facility_id, is_candidate):
    opensearch = OpenSearchServiceConnection()
    try:
        response = opensearch.client.delete(
            index=OpenSearchIndexNames.PRODUCTION_LOCATIONS_INDEX,
            id=facility_id,
        )
    except NotFoundError:
        response = {'result': 'not_found', 'id': facility_id}
    except OpenSearchException as error:
        if is_candidate:
            log.error(
                '[Location Deletion] OpenSearch error while deleting '
                'candidate %s; continuing, the document is not expected '
                'to exist: %s',
                facility_id,
                error,
            )
            return
        error_log_message = (
            '[Location Deletion] OpenSearch error; the document of '
            f'{facility_id} may now be orphaned: {error}'
        )
        log.error(error_log_message)
        report_error_to_rollbar(
            message=error_log_message,
            extra_data={'id': facility_id},
        )
        return

    if response and response.get('result') == 'not_found':
        if is_candidate:
            log.debug(
                '[Location Deletion] Candidate %s had no OpenSearch '
                'document, as expected.',
                facility_id,
            )
            return
        signal_error_notifier(
            '[Location Deletion] Facility not found in OpenSearch, '
            'indicating data inconsistency.',
            response,
        )


@receiver(post_save, sender=ModerationEvent)
def moderation_event_update_handler_for_opensearch(
    instance,
    created,
    **kwargs
):
    """
    Front-run the Logstash pipeline with the new status, after commit.

    A pure latency optimisation: the next Logstash run re-derives the same
    document from Postgres, so any OpenSearch error is logged and dropped.
    """
    if created:
        return

    event_id = str(instance.uuid)
    doc = {'status': str(instance.status), 'os_id': instance.os_id}
    transaction.on_commit(
        lambda: _update_moderation_event_document(event_id, doc)
    )


def _update_moderation_event_document(event_id, doc):
    opensearch = OpenSearchServiceConnection()
    try:
        response = opensearch.client.update(
            index=OpenSearchIndexNames.MODERATION_EVENTS_INDEX,
            id=event_id,
            body={'doc': doc},
        )
    except NotFoundError:
        log.error(
            '[Moderation Event Updating] '
            'ModerationEvent not found in OpenSearch.'
        )
        return
    except OpenSearchException as error:
        log.error(
            '[Moderation Event Updating] OpenSearch error; Logstash will '
            'catch up: %s',
            error,
        )
        return

    if response and response.get('result') == 'not_found':
        error_log_message = (
            "[Moderation Event Updating] "
            "ModerationEvent not found in OpenSearch, "
            "indicating data inconsistency."
        )
        signal_error_notifier(error_log_message, response)


def set_origin_source_on_create(instance, created, **kwargs):
    if created and instance.origin_source is None:
        instance.origin_source = settings.INSTANCE_SOURCE
        instance.save(update_fields=['origin_source'])


for model in [
    FacilityListItem,
    ExtendedField,
    FacilityClaim,
    FacilityAlias,
    FacilityActivityReport,
    FacilityLocation
]:
    post_save.connect(set_origin_source_on_create, sender=model)


@receiver(
    post_delete,
    sender=FacilityClaimAttachments,
    dispatch_uid='claim_attachment_file_cleanup',
)
def claim_attachment_post_delete_file_cleanup(instance, **kwargs):
    """
    Remove the stored file when an attachment row is deleted. Django
    emulates FK cascades in Python, so this also runs for every
    attachment removed by deleting its claim (e.g. facility deletion).
    save=False because the row is already gone.
    """
    if instance.claim_attachment:
        try:
            instance.claim_attachment.delete(save=False)
        except Exception:
            # The row is already gone, so the in-memory instance is the
            # last thing that knows which S3 object this was. Log the
            # storage key: without it, recovering an orphaned object
            # would mean diffing the whole bucket against the table.
            storage_key = instance.claim_attachment.name
            log.exception(
                'Failed to delete stored file for claim attachment %s '
                '(claim %s, storage key %s)',
                instance.pk,
                instance.claim_id,
                storage_key,
            )
            report_error_to_rollbar(
                message='Failed to delete stored claim attachment file',
                extra_data={
                    'attachment_id': instance.pk,
                    'claim_id': instance.claim_id,
                    'storage_key': storage_key,
                    'file_name': instance.file_name,
                },
            )


@receiver(
    pre_delete,
    sender=Contributor,
    dispatch_uid='close_deleted_contributor_claim_campaigns',
)
def close_deleted_contributor_claim_campaigns(instance, **kwargs):
    """
    A deleted contributor's claim campaigns are kept, with no owner
    (ClaimCampaign.contributor is SET_NULL), so their claims stay
    attributed. Close them first so they stop collecting new claims.
    Runs for every way a contributor is deleted: the
    delete_user_and_contributor command, Django admin, or a cascade.
    """
    # Imported here: the service imports api.models, which this module's
    # import is part of loading.
    from api.services.claim_campaign_service import (
        close_contributor_campaigns,
    )
    close_contributor_campaigns(instance)
