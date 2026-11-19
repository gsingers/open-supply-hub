from django.core.management.base import BaseCommand

from api.facility_search.indexer import FacilitySearchIndexer
from api.services.opensearch.opensearch import OpenSearchServiceConnection


class Command(BaseCommand):
    help = (
        'Apply api_facilitysearchoutbox entries to the facilities '
        'OpenSearch index. Runs forever unless --once is given.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true',
                            help='Drain the outbox, then exit.')
        parser.add_argument('--batch-size', type=int, default=500)
        parser.add_argument('--lease-seconds', type=float, default=300,
                            help='Take over claims older than this.')
        parser.add_argument('--idle-seconds', type=float, default=2,
                            help='Wait this long when the outbox is empty.')

    def handle(self, *args, **options):
        indexer = FacilitySearchIndexer(
            OpenSearchServiceConnection().client,
            batch_size=options['batch_size'],
            lease_seconds=options['lease_seconds'],
        )
        indexer.run(once=options['once'],
                    idle_seconds=options['idle_seconds'])
