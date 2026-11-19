"""
The `facilities` OpenSearch index that backs /map search and grid tiles.

Each document mirrors one `api_facilityindex` row (the table the legacy
`/api/facilities/` search filters), restricted to what filtering, sorting,
the extent and the tiles need. Responses are still serialized from
Postgres, so presentation fields are not indexed.

Readers use the `facilities` alias. A full rebuild writes a new concrete
`facilities-v<N>` index, which the `facilities-building` alias marks so the
outbox indexer writes changes to it too, then swaps `facilities` over.
"""

from django.conf import settings


def read_alias():
    # Overridable so tests can use their own indices on a shared cluster.
    return settings.FACILITIES_SEARCH_INDEX


def building_alias():
    return f'{read_alias()}-building'


def concrete_index_prefix():
    return f'{read_alias()}-v'


# Shortest `q` the trigram field can answer; shorter terms use a wildcard
# query on the normalized keyword.
NGRAM_SIZE = 3

KEYWORD_ARRAY_FIELDS = (
    'country_code',
    'contrib_types',
    'sector',
    'product_type',
    'number_of_workers',
    'facility_type',
    'parent_company_name',
    'isic_section',
    'isic_division',
    'isic_group',
    'isic_class',
    'isic5_section',
    'isic5_division',
    'isic5_group',
    'isic5_class',
    'isic_mapped_section',
    'isic_mapped_division',
    'isic_mapped_group',
    'isic_mapped_class',
)

INTEGER_ARRAY_FIELDS = (
    'contributors_id',
    'lists',
    'parent_company_id',
    'zone_set_ids',
)

ANALYSIS = {
    'char_filter': {
        # The legacy sort uses the database collation (en_US.UTF-8),
        # which ignores spaces and punctuation at the first level.
        'strip_non_alphanumeric': {
            'type': 'pattern_replace',
            'pattern': '[^\\p{L}\\p{Nd}]',
            'replacement': '',
        },
    },
    'tokenizer': {
        # Every substring of NGRAM_SIZE characters, spaces included, at
        # consecutive positions: a match_phrase over the query's grams is
        # a substring match, like the legacy UNACCENT(name) ILIKE '%q%'.
        'trigram': {
            'type': 'ngram',
            'min_gram': NGRAM_SIZE,
            'max_gram': NGRAM_SIZE,
            'token_chars': [],
        },
    },
    'analyzer': {
        'folded_trigram': {
            'type': 'custom',
            'tokenizer': 'trigram',
            'filter': ['lowercase', 'asciifolding'],
        },
        'lowercase_trigram': {
            'type': 'custom',
            'tokenizer': 'trigram',
            'filter': ['lowercase'],
        },
    },
    'normalizer': {
        'folded': {
            'type': 'custom',
            'filter': ['lowercase', 'asciifolding'],
        },
        'lowercase': {
            'type': 'custom',
            'filter': ['lowercase'],
        },
        'collation_key': {
            'type': 'custom',
            'char_filter': ['strip_non_alphanumeric'],
            'filter': ['lowercase', 'asciifolding'],
        },
    },
}


def _mappings():
    properties = {
        'os_id': {'type': 'keyword'},
        'name': {
            'type': 'keyword',
            'fields': {
                'trigram': {'type': 'text', 'analyzer': 'folded_trigram'},
                'folded': {'type': 'keyword', 'normalizer': 'folded'},
                'sort': {'type': 'keyword', 'normalizer': 'collation_key'},
            },
        },
        # Breaks ties between names equal under name.sort the way the
        # database collation does; see documents.collation_tiebreak.
        'name_tiebreak': {'type': 'keyword'},
        'native_language_name': {
            'type': 'keyword',
            'fields': {
                'trigram': {
                    'type': 'text',
                    'analyzer': 'lowercase_trigram',
                },
                'lowercase': {'type': 'keyword', 'normalizer': 'lowercase'},
            },
        },
        'processing_type': {
            'type': 'keyword',
            'fields': {
                'lowercase': {'type': 'keyword', 'normalizer': 'lowercase'},
            },
        },
        'contributors_count': {'type': 'integer'},
        'location': {'type': 'geo_point'},
        'is_closed': {'type': 'boolean'},
    }
    for field in KEYWORD_ARRAY_FIELDS:
        properties[field] = {'type': 'keyword'}
    for field in INTEGER_ARRAY_FIELDS:
        properties[field] = {'type': 'integer'}
    return {'dynamic': 'strict', 'properties': properties}


def index_body(number_of_replicas=1):
    return {
        'settings': {
            'number_of_shards': 1,
            'number_of_replicas': number_of_replicas,
            'max_ngram_diff': 0,
            'analysis': ANALYSIS,
        },
        'mappings': _mappings(),
    }


def concrete_index_name(version):
    return f'{concrete_index_prefix()}{version}'


def parse_version(index_name):
    prefix = concrete_index_prefix()
    if not index_name.startswith(prefix):
        return None
    suffix = index_name[len(prefix):]
    return int(suffix) if suffix.isdigit() else None
