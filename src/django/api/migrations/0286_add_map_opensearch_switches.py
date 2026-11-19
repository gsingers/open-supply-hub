from django.db import migrations


SWITCH_NAMES = (
    'map_opensearch_search',
    'map_opensearch_tiles',
    'map_opensearch_shadow',
)


def create_switches(apps, schema_editor):
    Switch = apps.get_model('waffle', 'Switch')
    for name in SWITCH_NAMES:
        Switch.objects.get_or_create(name=name, defaults={'active': False})


def delete_switches(apps, schema_editor):
    Switch = apps.get_model('waffle', 'Switch')
    Switch.objects.filter(name__in=SWITCH_NAMES).delete()


class Migration(migrations.Migration):
    """
    Switches that move /map search and grid tiles onto the facilities
    OpenSearch index (api/facility_search/backend.py). Created inactive,
    so deploying this changes nothing: Postgres keeps serving /map until
    a switch is turned on, and turning it off again is instant.
    """

    dependencies = [
        ('api', '0285_facility_search_outbox'),
    ]

    operations = [
        migrations.RunPython(create_switches, delete_switches),
    ]
