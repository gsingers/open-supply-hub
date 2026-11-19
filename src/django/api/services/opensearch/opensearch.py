import os
import logging
import threading
from distutils.util import strtobool

import boto3
from botocore.exceptions import ProfileNotFound
from opensearchpy import OpenSearch, RequestsHttpConnection, AWSV4SignerAuth
from django.conf import settings

log = logging.getLogger(__name__)


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
        self.__client = OpenSearch(
            hosts=[{'host': host, 'port': port}],
            http_auth=auth,
            use_ssl=use_ssl,
            verify_certs=verify_certs,
            connection_class=RequestsHttpConnection,
            # Bound how long a slow or unreachable cluster can hold a
            # worker; callers on hot paths pass a tighter request_timeout.
            timeout=settings.OPENSEARCH_TIMEOUT_SECONDS,
            max_retries=settings.OPENSEARCH_MAX_RETRIES,
            retry_on_timeout=True,
            pool_maxsize=settings.OPENSEARCH_POOL_MAXSIZE,
        )

    @property
    def client(self) -> OpenSearch:
        return self.__client

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
