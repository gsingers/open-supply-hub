import os
import logging
import threading
from distutils.util import strtobool

import boto3
from botocore.exceptions import ProfileNotFound
from opensearchpy import OpenSearch, RequestsHttpConnection, AWSV4SignerAuth
from django.conf import settings

log = logging.getLogger(__name__)


def client_options(for_map=False):
    """
    Transport options. The default client keeps opensearch-py's own
    timeout and retry behaviour, which the v1 search, moderation and
    rebuild callers were written against, and only sizes the pool. The
    map client (search list, tiles) never retries: a slow cluster should
    send the request to Postgres after one request_timeout, not after
    several.
    """
    options = {
        'timeout': settings.OPENSEARCH_TIMEOUT_SECONDS,
        'max_retries': settings.OPENSEARCH_MAX_RETRIES,
        'retry_on_timeout': False,
        'pool_maxsize': settings.OPENSEARCH_POOL_MAXSIZE,
    }
    if for_map:
        options.update(
            timeout=settings.MAP_OPENSEARCH_REQUEST_TIMEOUT_SECONDS,
            max_retries=0,
        )
    return options


class OpenSearchServiceConnection:
    '''
    The singleton class configures the connection between
    Django and OpenSearch. It exposes a `client` property
    for communicating with the OpenSearch cluster.
    '''

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    instance = super(OpenSearchServiceConnection, cls) \
                        .__new__(cls)
                    instance.__initialize()
                    cls._instance = instance
        return cls._instance

    def __initialize(self):
        auth = None
        if not getattr(settings, 'DEBUG', False):
            # Set up AWS authentication only if the application is running in
            # the AWS cloud.
            auth = self.__get_aws_auth()

        host = os.getenv('OPENSEARCH_HOST')
        port = os.getenv('OPENSEARCH_PORT')
        use_ssl = bool(strtobool(
            os.getenv('OPENSEARCH_SSL', 'False')
        ))
        verify_certs = bool(strtobool(
            os.getenv('OPENSEARCH_SSL_CERT_VERIFICATION', 'False')
        ))
        self.__connection_kwargs = {
            'hosts': [{'host': host, 'port': port}],
            'http_auth': auth,
            'use_ssl': use_ssl,
            'verify_certs': verify_certs,
            'connection_class': RequestsHttpConnection,
        }
        self.__client = OpenSearch(
            **self.__connection_kwargs, **client_options()
        )
        self.__map_client = None

    @property
    def client(self) -> OpenSearch:
        return self.__client

    @property
    def map_client(self) -> OpenSearch:
        """The no-retry client for /map requests (see client_options)."""
        if self.__map_client is None:
            with self._lock:
                if self.__map_client is None:
                    self.__map_client = OpenSearch(
                        **self.__connection_kwargs,
                        **client_options(for_map=True),
                    )
        return self.__map_client

    @staticmethod
    def __get_aws_auth() -> AWSV4SignerAuth:
        try:
            # While running in the AWS environment, the auto-generated AWS
            # credentials for the IAM role assigned to the instance are used.
            credentials = boto3.Session().get_credentials()
        except ProfileNotFound:
            log.error(('[OpenSearch Connection] AWS creds were not found for '
                       'the connection with the OpenSearch cluster.'))
            raise

        region = os.getenv('AWS_DEFAULT_REGION')
        try:
            aws_auth = AWSV4SignerAuth(credentials, region)
        except ValueError:
            log.error(('[OpenSearch Connection] The credentials and AWS '
                       'region values are empty.'))
            raise

        return aws_auth
