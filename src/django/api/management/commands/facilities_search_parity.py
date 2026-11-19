import json

from django.core.management.base import BaseCommand

from api.facility_search.parity import run
from api.services.opensearch.opensearch import OpenSearchServiceConnection


class Command(BaseCommand):
    help = (
        'Run /api/facilities/ query strings (one per line: a bare query '
        'string, ?query or URL) through Postgres and the facilities '
        'OpenSearch index, and report count, top-N id and extent '
        'mismatches per parameter. Read-only on both sides.'
    )

    def add_arguments(self, parser):
        parser.add_argument('input', help='File of query strings.')
        parser.add_argument('--top', type=int, default=50,
                            help='Ids compared per query (page size).')
        parser.add_argument('--timeout', type=float, default=30)
        parser.add_argument('--json', dest='json_path',
                            help='Also write the full report as JSON.')

    def handle(self, *args, **options):
        with open(options['input']) as lines:
            report = run(OpenSearchServiceConnection().client, lines,
                         top=options['top'], timeout=options['timeout'])

        header = ('param', 'queries', 'routed', 'errors', 'count!=',
                  'ids!=', 'extent!=')
        self.stdout.write('{:<24}{:>9}{:>8}{:>8}{:>9}{:>7}{:>10}'.format(
            *header))
        for name, row in report.by_param().items():
            self.stdout.write(
                '{:<24}{:>9}{:>8}{:>8}{:>9}{:>7}{:>10}'.format(
                    name, row.queries, row.routed, row.errors,
                    row.count_mismatches, row.id_mismatches,
                    row.extent_mismatches))
        for comparison in report.comparisons:
            if comparison.mismatched or comparison.error:
                self.stdout.write(
                    f'MISMATCH {comparison.query}: postgres='
                    f'{comparison.postgres_count} opensearch='
                    f'{comparison.opensearch_count} ids='
                    f'{comparison.ids_match} (overlap '
                    f'{comparison.id_overlap}) extent='
                    f'{comparison.extent_match} error={comparison.error}')
        if options['json_path']:
            with open(options['json_path'], 'w') as out:
                json.dump(report.as_dict(), out, indent=2)
