"""
The filter-values index behind entity typeahead: one document per
country, public contributor, parent company, sector, product type,
facility type, processing type and contributor type that occurs in the
`facilities` index, with its label and how many facilities have it.

Built from the `facilities` index itself (composite aggregations), so it
only offers values that exist, then swapped in under an alias. It is small
(tens of thousands of documents) and rebuilt on a schedule; counts shown
to users are recomputed under the current filters at query time
(suggest.py), so staleness only affects which values are offered.
"""
import logging
import time
from dataclasses import dataclass
from typing import Callable, Dict, Iterator, Optional

from opensearchpy.exceptions import NotFoundError
from opensearchpy.helpers import bulk

from api.facility_search.index_definition import ANALYSIS, read_alias
from api.facility_search.ux.text import sort_key

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ValueKind:
    kind: str       # document kind, and the suggestion group
    field: str      # facilities index field
    group: str      # filter group (query.build_filter_groups)
    param: str      # /api/facilities/ param that filters on it


KINDS = (
    ValueKind('country', 'country_code', 'countries', 'countries'),
    ValueKind('contributor', 'contributors_id', 'contributors',
              'contributors'),
    ValueKind('parent_company', 'parent_company_name', 'parent_company',
              'parent_company'),
    ValueKind('sector', 'sector', 'sectors', 'sectors'),
    ValueKind('product_type', 'product_type', 'product_type',
              'product_type'),
    ValueKind('facility_type', 'facility_type', 'facility_processing',
              'facility_type'),
    ValueKind('processing_type', 'processing_type', 'facility_processing',
              'processing_type'),
    ValueKind('contributor_type', 'contrib_types', 'contributor_types',
              'contributor_types'),
)
KINDS_BY_NAME = {kind.kind: kind for kind in KINDS}

MAPPINGS = {
    'dynamic': 'strict',
    'properties': {
        'kind': {'type': 'keyword'},
        'value': {'type': 'keyword'},
        'label': {'type': 'keyword'},
        'label_key': {'type': 'keyword'},
        'label_text': {
            'type': 'text',
            'analyzer': 'folded_text',
            'fields': {'prefix': {
                'type': 'text', 'analyzer': 'prefix_index',
                'search_analyzer': 'folded_text',
            }},
        },
        'doc_count': {'type': 'rank_feature'},
    },
}


def values_alias() -> str:
    return f'{read_alias()}_values'


def _composite(client, index: str, field: str) -> Iterator[tuple]:
    """Every (value, doc count) of `field`, paged."""
    after = None
    while True:
        composite = {'size': 1000,
                     'sources': [{'v': {'terms': {'field': field}}}]}
        if after:
            composite['after'] = after
        response = client.search(index=index, body={
            'size': 0, 'aggs': {'values': {'composite': composite}},
        }, request_timeout=120)
        agg = response['aggregations']['values']
        for bucket in agg['buckets']:
            yield bucket['key']['v'], bucket['doc_count']
        after = agg.get('after_key')
        if not after or not agg['buckets']:
            return


def _documents(client, labels: Dict[str, Callable[[str], Optional[str]]]):
    for kind in KINDS:
        label_of = labels.get(kind.kind, lambda value: value)
        for value, count in _composite(client, read_alias(), kind.field):
            label = label_of(str(value))
            if not label:
                continue  # e.g. a contributor the public list hides
            yield {
                '_id': f'{kind.kind}|{value}',
                'kind': kind.kind,
                'value': str(value),
                'label': label,
                'label_key': sort_key(label),
                'label_text': label,
                'doc_count': max(count, 1),
            }


def rebuild(client, labels: Dict[str, Callable[[str], Optional[str]]],
            replicas: int = 1, keep_previous: bool = False) -> str:
    """
    Build a new values index and point the alias at it. `labels` maps a
    kind to a function from value to label (None drops the value); kinds
    without one use the value as its label.
    """
    alias = values_alias()
    index = f'{alias}-{int(time.time())}'
    client.indices.create(index=index, body={
        'settings': {'number_of_shards': 1, 'number_of_replicas': 0,
                     'analysis': ANALYSIS},
        'mappings': MAPPINGS,
    })
    actions = ({'_index': index, **doc} for doc in _documents(client, labels))
    indexed, _ = bulk(client, actions, chunk_size=2000, request_timeout=120)
    client.indices.refresh(index=index)
    client.indices.put_settings(index=index,
                                body={'index': {'number_of_replicas':
                                                replicas}})
    try:
        old = [name for name in client.indices.get_alias(name=alias)
               if name != index]
    except NotFoundError:
        old = []
    actions = [{'add': {'index': index, 'alias': alias}}]
    actions += [{'remove': {'index': name, 'alias': alias}} for name in old]
    client.indices.update_aliases(body={'actions': actions})
    if not keep_previous:
        for name in old:
            client.indices.delete(index=name)
    log.info('[Search UX] %s values in %s', indexed, index)
    return index
