"""
Disjunctive facet counts for the /map filters, in one request.

The text (scored or not) is the query; every filter group goes into the
aggregations, so each count can leave out exactly the groups it needs:

* `_total`: every group, which is the result count the list shows.
* `f_<facet>`: every group except the facet's own, so choosing India
  does not zero out Bangladesh in the country counts.
* `r_<group>`: every group except one, for each active group: how many
  results there would be without that filter (zero-result recovery).

Selected values are always counted, even when they fall outside the top
N, so a chosen filter never shows without its count.
"""
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence

from api.constants import FacilitiesQueryParams as P
from api.facility_search.query import flatten
from api.facility_search.ux import assets
from api.facility_search.ux.relevance import RELEVANCE
from api.facility_search.ux.text import filter_groups, scored_text, text_params
from api.helpers.helpers import clean

DEFAULT_SIZE = 10
MAX_SIZE = 50


@dataclass(frozen=True)
class Facet:
    key: str       # response key, also the client's facet id
    group: str     # the filter group (query.build_filter_groups) it owns
    field: str     # index field it counts
    param: str     # request param whose values are its selections


FACETS = (
    Facet('countries', 'countries', 'country_code', P.COUNTRIES),
    Facet('sectors', 'sectors', 'sector', P.SECTOR),
    Facet('contributor_types', 'contributor_types', 'contrib_types',
          P.CONTRIBUTOR_TYPES),
    Facet('contributors', 'contributors', 'contributors_id', P.CONTRIBUTORS),
    Facet('parent_company', 'parent_company', 'parent_company_name',
          P.PARENT_COMPANY),
    Facet('facility_type', 'facility_processing', 'facility_type',
          P.FACILITY_TYPE),
    Facet('processing_type', 'facility_processing', 'processing_type',
          P.PROCESSING_TYPE),
    Facet('product_type', 'product_type', 'product_type', P.PRODUCT_TYPE),
    Facet('number_of_workers', 'number_of_workers', 'number_of_workers',
          P.NUMBER_OF_WORKERS),
    Facet('claim_status', 'claim_status', 'claim_status', P.CLAIM_STATUS),
)
FACETS_BY_KEY = {facet.key: facet for facet in FACETS}


def requested_facets(keys: Iterable[str], embed: bool) -> List[Facet]:
    """Known facets among `keys` (all when none). Embed pins contributors."""
    keys = [key for key in keys if key in FACETS_BY_KEY]
    facets = [FACETS_BY_KEY[k] for k in keys] if keys else list(FACETS)
    if embed:
        facets = [f for f in facets if f.key != 'contributors']
    return facets


def _selected(params, facet: Facet) -> list:
    values = params.getlist(facet.param)
    if facet.key == 'contributors':
        return [int(v) for v in values if v.isdigit()]
    if facet.key == 'product_type':
        return [clean(v) for v in values]
    if facet.key == 'parent_company':
        return [v for v in values if not v.isnumeric()]
    return values


def _bool_filter(clauses: List[dict]) -> dict:
    return {'bool': {'filter': clauses}}


def _facet_agg(params, facet: Facet, groups: Dict[str, List[dict]],
               size: int, allowed_contributors: Optional[Sequence[int]]):
    terms = {'field': facet.field, 'size': size}
    if facet.key == 'contributors':
        # Only contributors the public list shows may be counted.
        terms['include'] = list(allowed_contributors or [])
    aggs = {'top': {'terms': terms}}
    selected = _selected(params, facet)
    if selected:
        aggs['selected'] = {'terms': {
            'field': facet.field, 'include': selected,
            'size': len(selected),
        }}
    return {
        'filter': _bool_filter(flatten(groups, without={facet.group})),
        'aggs': aggs,
    }


def build_aggs(params, facets: List[Facet], groups: Dict[str, List[dict]],
               size: int, allowed_contributors=None, pinned=()) -> dict:
    aggs = {'_total': {'filter': _bool_filter(flatten(groups))}}
    for facet in facets:
        aggs[f'f_{facet.key}'] = _facet_agg(
            params, facet, groups, size, allowed_contributors)
    for group in groups:
        if group not in pinned:
            aggs[f'r_{group}'] = {
                'filter': _bool_filter(flatten(groups, without={group})),
            }
    return aggs


def template_params(params, sort_by: Optional[str], facets: List[Facet],
                    size: int, allowed_contributors=None) -> dict:
    """Parameters for the search template. Raises UnsupportedQuery."""
    text = scored_text(params) if sort_by == RELEVANCE else None
    groups = filter_groups(params, text)
    pinned = {'contributors'} if P.EMBED in params else set()
    return {
        'from': 0,
        'size': 0,
        'filters': [],
        'sort': ['_score'],
        'aggs': build_aggs(params, facets, groups, size,
                           allowed_contributors, pinned),
        'highlight': False,
        'suggest': text is not None,
        **text_params(params, text),
    }


def _buckets(agg: dict) -> List[dict]:
    return [{'value': b['key'], 'count': b['doc_count']}
            for b in agg.get('buckets', [])]


def _parse_facet(agg: dict) -> dict:
    values = _buckets(agg['top'])
    seen = {v['value'] for v in values}
    selected = _buckets(agg.get('selected', {}))
    return {
        'values': values,
        # Selected values outside the top N, still counted.
        'selected': [v for v in selected if v['value'] not in seen],
        'other': agg['top'].get('sum_other_doc_count', 0),
        'total': agg['doc_count'],
    }


def _did_you_mean(response: dict) -> Optional[dict]:
    for entry in response.get('suggest', {}).get('did_you_mean', []):
        for option in entry.get('options', []):
            return {'text': option['text'],
                    'highlighted': option.get('highlighted', option['text'])}
    return None


def parse(response: dict, facets: List[Facet]) -> dict:
    aggs = response['aggregations']
    relaxations = [
        {'group': key[2:], 'count': agg['doc_count']}
        for key, agg in aggs.items() if key.startswith('r_')
    ]
    return {
        'total': aggs['_total']['doc_count'],
        'facets': {facet.key: _parse_facet(aggs[f'f_{facet.key}'])
                   for facet in facets},
        'relaxations': sorted(relaxations, key=lambda r: -r['count']),
        'did_you_mean': _did_you_mean(response),
    }


def run(client, index: str, template: dict, facets: List[Facet],
        timeout: float) -> dict:
    response = assets.search_template(
        client, index, assets.search_template_id(), template,
        assets.search_pipeline_id(), timeout,
    )
    return parse(response, facets)
