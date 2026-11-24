from django.db import migrations


SWITCH_NAME = 'map_search_ux'


def create_switch(apps, schema_editor):
    Switch = apps.get_model('waffle', 'Switch')
    Switch.objects.get_or_create(name=SWITCH_NAME, defaults={'active': False})


def delete_switch(apps, schema_editor):
    Switch = apps.get_model('waffle', 'Switch')
    Switch.objects.filter(name=SWITCH_NAME).delete()


class Migration(migrations.Migration):
    """
    The switch for the /map search UX (facet counts, typeahead, scored
    search, instant search; api/facility_search/ux). Created inactive.
    """

    dependencies = [
        ('api', '0286_add_map_opensearch_switches'),
    ]

    operations = [
        migrations.RunPython(create_switch, delete_switch),
    ]
