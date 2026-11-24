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

# Keyword fields the search UX facets on: loaded eagerly so the first
# aggregation after a refresh does not pay for building global ordinals.
FACET_KEYWORD_FIELDS = (
    'country_code',
    'contrib_types',
    'sector',
    'product_type',
    'number_of_workers',
    'facility_type',
    'parent_company_name',
    'processing_type',
    'claim_status',
)

# Keyword fields that also get a scored text subfield (`<field>.text`).
TEXT_SUBFIELD_FIELDS = ('parent_company_name', 'product_type')

NAME_SYNONYMS = [
    'ltd, limited',
    'pvt, private',
    'co, company',
    'corp, corporation',
    'inc, incorporated',
    'intl, international',
    'mfg, manufacturing',
    'ind, industries, industry',
    'exp, export, exports',
    'fty, factory',
    'garment, garments',
    'textile, textiles',
    'mill, mills',
]

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
        # For text already folded by Postgres (see name_search).
        'plain_trigram': {
            'type': 'custom',
            'tokenizer': 'trigram',
        },
        'lowercase_trigram': {
            'type': 'custom',
            'tokenizer': 'trigram',
            'filter': ['lowercase'],
        },
        # Scored text matching for the search UX (ux/): words, lower case,
        # accents folded. ICU folding would be better for non-Latin
        # scripts; AWS OpenSearch ships analysis-icu, the stock docker
        # image does not, so the prototype uses asciifolding.
        'folded_text': {
            'type': 'custom',
            'tokenizer': 'standard',
            'filter': ['lowercase', 'asciifolding'],
        },
        'folded_text_search': {
            'type': 'custom',
            'tokenizer': 'standard',
            'filter': ['lowercase', 'asciifolding', 'name_synonyms'],
        },
        # Typeahead: every word indexed as its prefixes, queried whole.
        'prefix_index': {
            'type': 'custom',
            'tokenizer': 'standard',
            'filter': ['lowercase', 'asciifolding', 'prefix_edge'],
        },
    },
    'filter': {
        # Search-time only, so the list can change with a reindex-free
        # close/open of the index. Abbreviations that facility names use
        # interchangeably; see ux/relevance.py for how they are weighted.
        'name_synonyms': {
            'type': 'synonym_graph',
            'synonyms': NAME_SYNONYMS,
        },
        'prefix_edge': {
            'type': 'edge_ngram',
            'min_gram': 1,
            'max_gram': 20,
        },
    },
    'normalizer': {
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
                'sort': {'type': 'keyword', 'normalizer': 'collation_key'},
            },
        },
        # UPPER(UNACCENT(name)) computed by Postgres when the document is
        # built, so q matches exactly what the legacy
        # UPPER(UNACCENT(name)) LIKE UPPER(UNACCENT('%q%')) matches:
        # decomposed accents, one-to-many folds (ß, Æ) and symbols
        # UNACCENT rewrites (®) included. Queries are folded the same way.
        'name_search': {
            'type': 'keyword',
            'fields': {
                'trigram': {'type': 'text', 'analyzer': 'plain_trigram'},
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
        # --- Search UX (ux/): scored text, typeahead, facets, ranking ---
        'name_text': {
            'type': 'text',
            'analyzer': 'folded_text',
            'search_analyzer': 'folded_text_search',
            'fields': {
                'prefix': {
                    'type': 'text',
                    'analyzer': 'prefix_index',
                    'search_analyzer': 'folded_text',
                },
            },
        },
        'address_text': {
            'type': 'text',
            'analyzer': 'folded_text',
            'search_analyzer': 'folded_text_search',
        },
        # 'claimed' or 'unclaimed'.
        'claim_status': {'type': 'keyword'},
        # contributors_count as a ranking signal; absent when 0, since a
        # rank_feature must be positive.
        'popularity': {'type': 'rank_feature'},
        # UNACCENT(custom_text_search): the embed-mode free text, entries
        # '<contributor id>|<lower-cased value>' joined by spaces.
        'custom_text_search': {'type': 'keyword', 'ignore_above': 8000},
    }
    properties['native_language_name']['fields']['text'] = {
        'type': 'text', 'analyzer': 'folded_text',
    }
    for field in KEYWORD_ARRAY_FIELDS:
        properties[field] = {'type': 'keyword'}
    for field in INTEGER_ARRAY_FIELDS:
        properties[field] = {'type': 'integer'}
    for field in FACET_KEYWORD_FIELDS:
        properties[field]['eager_global_ordinals'] = True
    for field in TEXT_SUBFIELD_FIELDS:
        properties[field].setdefault('fields', {})['text'] = {
            'type': 'text',
            'analyzer': 'folded_text',
            'search_analyzer': 'folded_text_search',
        }
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
