from django.core.management.base import BaseCommand

from api.facility_search.index_definition import (
    read_alias,
    concrete_index_name,
)
from api.facility_search.rebuild import alias_indices, rebuild, swap_alias
from api.services.opensearch.opensearch import OpenSearchServiceConnection


class Command(BaseCommand):
    help = (
        'Build a new facilities-v<N> OpenSearch index from '
        'api_facilityindex and point the facilities alias at it. Also '
        'shows the current state (--status) or moves the alias back to an '
        'earlier version (--point-alias-to).'
    )

    def add_arguments(self, parser):
        parser.add_argument('--version', type=int,
                            help='Version to build; default is next.')
        parser.add_argument('--batch-size', type=int, default=2000)
        parser.add_argument('--replicas', type=int, default=1)
        parser.add_argument('--no-swap', action='store_true',
                            help='Build, but leave the alias where it is.')
        parser.add_argument('--delete-old', action='store_true',
                            help='Delete the previous index after the swap.')
        parser.add_argument('--status', action='store_true')
        parser.add_argument('--point-alias-to', type=int, metavar='VERSION',
                            help='Point the alias at an existing version.')

    def handle(self, *args, **options):
        client = OpenSearchServiceConnection().client
        if options['status']:
            self.stdout.write(
                f'{read_alias()} -> {alias_indices(client, read_alias())}'
            )
            return
        if options['point_alias_to'] is not None:
            index = concrete_index_name(options['point_alias_to'])
            old = swap_alias(client, index)
            self.stdout.write(f'{read_alias()} -> {index} (was {old})')
            return
        rebuild(
            client,
            version=options['version'],
            batch_size=options['batch_size'],
            replicas=options['replicas'],
            swap=not options['no_swap'],
            delete_old=options['delete_old'],
            report=self.stdout.write,
        )
