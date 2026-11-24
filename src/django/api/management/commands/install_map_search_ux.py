from django.core.management.base import BaseCommand

from countries.lib.countries import COUNTRY_NAMES
from api.facility_search.ux import assets, values
from api.services.opensearch.opensearch import OpenSearchServiceConnection
from api.views.contributor.active_contributors import active_contributors


class Command(BaseCommand):
    help = (
        'Install the search UX templates and search pipelines '
        '(MAP_SEARCH_UX_ASSET_VERSION) and rebuild the filter-values '
        'index used by typeahead. Run after rebuild_facilities_search_index '
        'and on a schedule to pick up new values.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--replicas', type=int, default=1)
        parser.add_argument('--assets-only', action='store_true',
                            help='Install templates and pipelines only.')

    def handle(self, *args, **options):
        client = OpenSearchServiceConnection().client
        for name in assets.install(client):
            self.stdout.write(f'installed {name}')
        if options['assets_only']:
            return
        contributors = dict(active_contributors().values_list('id', 'name'))
        labels = {
            'country': COUNTRY_NAMES.get,
            # Public contributors only; others are left out entirely.
            'contributor': lambda value: contributors.get(int(value)),
        }
        index = values.rebuild(client, labels, replicas=options['replicas'])
        self.stdout.write(f'values index {index} -> {values.values_alias()}')
