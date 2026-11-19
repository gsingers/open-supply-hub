from django.core.management.base import BaseCommand, CommandError

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
        # Not --version: Django reserves it for every command.
        parser.add_argument('--index-version', type=int,
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
        parser.add_argument(
            '--accept-stale', action='store_true',
            help=('Required with --point-alias-to. A previous index stops '
                  'receiving changes when it is swapped out, so it serves '
                  'the data as of that swap. A fresh rebuild (with the '
                  'code version wanted) is the way to an up-to-date '
                  'index.'),
        )

    def handle(self, *args, **options):
        client = OpenSearchServiceConnection().client
        if options['status']:
            self.stdout.write(
                f'{read_alias()} -> {alias_indices(client, read_alias())}'
            )
            return
        if options['point_alias_to'] is not None:
            index = concrete_index_name(options['point_alias_to'])
            if not options['accept_stale']:
                raise CommandError(
                    f'{index} has not received changes since it was '
                    'swapped out, so /map would serve stale results. Pass '
                    '--accept-stale to use it anyway (an emergency '
                    'rollback), or run a fresh rebuild instead.'
                )
            old = swap_alias(client, index)
            self.stdout.write(self.style.WARNING(
                f'{read_alias()} -> {index} (was {old}). {index} is stale '
                'until the outbox indexer catches it up from new changes; '
                'run a rebuild to bring it fully current.'
            ))
            return
        rebuild(
            client,
            version=options['index_version'],
            batch_size=options['batch_size'],
            replicas=options['replicas'],
            swap=not options['no_swap'],
            delete_old=options['delete_old'],
            report=self.stdout.write,
        )
