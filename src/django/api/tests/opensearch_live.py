"""
Helpers for tests that need a real OpenSearch node.

They run against OPENSEARCH_HOST (the docker compose single node) and are
skipped when it cannot be reached. Each test class works under its own
FACILITIES_SEARCH_INDEX name and deletes its indices afterwards, so a
developer's `facilities` index is never touched.
"""
import os
import uuid
from unittest import skipUnless

from django.test import override_settings
from opensearchpy import OpenSearch


def live_client():
    # The application client signs requests for AWS whenever DEBUG is
    # off, which it always is under the test runner; the compose node has
    # security disabled, so tests talk to it unsigned.
    return OpenSearch(
        hosts=[{'host': os.getenv('OPENSEARCH_HOST', 'localhost'),
                'port': os.getenv('OPENSEARCH_PORT', '9200')}],
        timeout=30,
    )


def _reachable():
    if not os.getenv('OPENSEARCH_HOST'):
        return False
    try:
        return bool(live_client().ping(request_timeout=2))
    except Exception:
        return False


OPENSEARCH_AVAILABLE = _reachable()

requires_opensearch = skipUnless(
    OPENSEARCH_AVAILABLE, 'OpenSearch is not reachable'
)


class LiveIndexMixin:
    """Gives each test a unique index name and cleans it up."""

    def setUp(self):
        super().setUp()
        self.index_name = f'test-facilities-{uuid.uuid4().hex[:8]}'
        name_override = override_settings(
            FACILITIES_SEARCH_INDEX=self.index_name
        )
        name_override.enable()
        self.addCleanup(name_override.disable)
        self.client = live_client()
        self.addCleanup(self._delete_indices)

    def _delete_indices(self):
        self.client.indices.delete(
            index=f'{self.index_name}-v*',
            ignore_unavailable=True,
            allow_no_indices=True,
        )
