"""
The OpenSearch signal handlers must never fail or roll back the Postgres
write they observe: they run after commit and report errors instead of
raising (map migration plan, section 4.4).
"""
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase
from opensearchpy.exceptions import (
    ConnectionError,
    ConnectionTimeout,
    NotFoundError,
    TransportError,
)

from api.signals import (
    location_post_delete_handler_for_opensearch,
    moderation_event_update_handler_for_opensearch,
)

CONNECTION = 'api.signals.OpenSearchServiceConnection'


def facility(is_candidate=False):
    return SimpleNamespace(id='US2026000000001', is_candidate=is_candidate)


def moderation_event():
    return SimpleNamespace(
        uuid='f65ec710-f7b9-4f50-b960-135a7ab24ee6',
        status='APPROVED',
        os_id='US2026000000001',
    )


class LocationDeleteHandlerTest(TestCase):
    def test_delete_waits_for_commit(self):
        with patch(CONNECTION) as connection, \
                self.captureOnCommitCallbacks(execute=False) as callbacks:
            location_post_delete_handler_for_opensearch(facility())
            connection.return_value.client.delete.assert_not_called()

        self.assertEqual(len(callbacks), 1)

    def test_delete_runs_after_commit(self):
        with patch(CONNECTION) as connection, \
                self.captureOnCommitCallbacks(execute=True):
            connection.return_value.client.delete.return_value = {
                'result': 'deleted'
            }
            location_post_delete_handler_for_opensearch(facility())

        connection.return_value.client.delete.assert_called_once_with(
            index='production-locations',
            id='US2026000000001',
        )

    def test_outage_for_confirmed_facility_is_reported_not_raised(self):
        for error in (
            ConnectionError('N/A', 'down', None),
            ConnectionTimeout('TIMEOUT', 'slow', None),
            TransportError(500, 'boom', {}),
        ):
            with self.subTest(error=type(error).__name__), \
                    patch(CONNECTION) as connection, \
                    patch('api.signals.report_error_to_rollbar') as report, \
                    self.captureOnCommitCallbacks(execute=True):
                connection.return_value.client.delete.side_effect = error
                location_post_delete_handler_for_opensearch(facility())

            report.assert_called_once()

    def test_outage_for_candidate_is_not_reported(self):
        with patch(CONNECTION) as connection, \
                patch('api.signals.report_error_to_rollbar') as report, \
                self.captureOnCommitCallbacks(execute=True):
            connection.return_value.client.delete.side_effect = (
                ConnectionError('N/A', 'down', None)
            )
            location_post_delete_handler_for_opensearch(facility(True))

        report.assert_not_called()

    def test_missing_document_for_confirmed_is_reported(self):
        with patch(CONNECTION) as connection, \
                patch('api.signals.signal_error_notifier') as notifier, \
                self.captureOnCommitCallbacks(execute=True):
            connection.return_value.client.delete.side_effect = (
                NotFoundError(404, 'not_found', {})
            )
            location_post_delete_handler_for_opensearch(facility())

        notifier.assert_called_once()


class ModerationEventUpdateHandlerTest(TestCase):
    def test_update_waits_for_commit_and_skips_creates(self):
        with patch(CONNECTION) as connection, \
                self.captureOnCommitCallbacks(execute=True) as callbacks:
            moderation_event_update_handler_for_opensearch(
                moderation_event(), created=True
            )

        self.assertEqual(callbacks, [])
        connection.return_value.client.update.assert_not_called()

    def test_update_runs_after_commit_without_phantom_uuid(self):
        with patch(CONNECTION) as connection, \
                self.captureOnCommitCallbacks(execute=True):
            moderation_event_update_handler_for_opensearch(
                moderation_event(), created=False
            )

        connection.return_value.client.update.assert_called_once_with(
            index='moderation-events',
            id='f65ec710-f7b9-4f50-b960-135a7ab24ee6',
            body={'doc': {
                'status': 'APPROVED',
                'os_id': 'US2026000000001',
            }},
        )

    def test_outage_is_logged_not_raised(self):
        with patch(CONNECTION) as connection, \
                self.captureOnCommitCallbacks(execute=True):
            connection.return_value.client.update.side_effect = (
                ConnectionError('N/A', 'down', None)
            )
            moderation_event_update_handler_for_opensearch(
                moderation_event(), created=False
            )


class CallbackNeverRaisesTest(TestCase):
    """An exception in an on_commit callback escapes after COMMIT."""

    def test_client_construction_failure_is_contained(self):
        with patch(CONNECTION, side_effect=ValueError('no creds')), \
                patch('api.signals.report_error_to_rollbar') as report, \
                self.captureOnCommitCallbacks(execute=True):
            location_post_delete_handler_for_opensearch(facility())
            moderation_event_update_handler_for_opensearch(
                moderation_event(), created=False
            )

        report.assert_called()

    def test_notifier_failure_is_contained(self):
        with patch(CONNECTION) as connection, \
                patch('api.signals.signal_error_notifier',
                      side_effect=RuntimeError('rollbar down')), \
                self.captureOnCommitCallbacks(execute=True):
            connection.return_value.client.delete.return_value = {
                'result': 'not_found'}
            connection.return_value.client.update.return_value = {
                'result': 'not_found'}
            location_post_delete_handler_for_opensearch(facility())
            moderation_event_update_handler_for_opensearch(
                moderation_event(), created=False
            )
