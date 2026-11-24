"""
Typeahead for the /map search box: facilities by name, and filter values
(countries, contributors, parent companies, sectors, ...) that the client
turns into filter chips instead of free text.

Everything is computed under the filters already applied, so a
suggestion never leads to zero results:

1. Facilities: the suggest template through the suggest pipeline, which
   collapses same-named sites to one suggestion.
2. Values: the values template on the filter-values index proposes
   candidates by label prefix, then one count request on `facilities`
   counts each candidate disjunctively (its own kind's filter replaced,
   the rest kept). Candidates with no results are dropped.
"""
import unicodedata
from typing import Dict, Iterable, List, Optional

from api.facility_search.query import build_filter_groups, flatten
from api.facility_search.ux import assets
from api.facility_search.ux.text import MAX_TEXT_LENGTH, sort_key
from api.facility_search.ux.values import KINDS_BY_NAME, values_alias
from api.os_id import string_matches_os_id_format

MAX_FACILITIES = 5
PER_KIND = 3
# Value groups shown, in order.
DEFAULT_KINDS = ('country', 'contributor', 'parent_company', 'sector',
                 'product_type', 'facility_type', 'processing_type')


def _is_wide_script(text: str) -> bool:
    """CJK and similar scripts carry a word in one or two characters."""
    return any(unicodedata.east_asian_width(ch) in ('W', 'F') for ch in text)


def normalize_query(text: Optional[str]) -> Optional[str]:
    """The query to suggest for, or None when it is too short."""
    text = (text or '').strip()[:MAX_TEXT_LENGTH]
    minimum = 1 if _is_wide_script(text) else 2
    return text if len(text) >= minimum else None


def _facility_suggestions(client, index, q, filters, timeout) -> List[dict]:
    response = assets.search_template(
        client, index, assets.suggest_template_id(),
        {'q': q, 'q_key': sort_key(q), 'size': MAX_FACILITIES,
         'filters': filters},
        assets.suggest_pipeline_id(), timeout,
    )
    return [{
        'os_id': hit['_source']['os_id'],
        'name': hit['_source']['name'],
        'country_code': hit['_source']['country_code'],
        'contributors_count': hit['_source']['contributors_count'],
    } for hit in response['hits']['hits']]


def _value_candidates(client, q, kinds, timeout) -> List[dict]:
    response = client.search_template(
        index=values_alias(),
        body={'id': assets.values_template_id(), 'params': {
            'q': q, 'q_key': sort_key(q), 'kinds': list(kinds),
            'per_kind': PER_KIND,
        }},
        request_timeout=timeout,
    )
    candidates = []
    for bucket in response['aggregations']['kinds']['buckets']:
        for hit in bucket['top']['hits']['hits']:
            candidates.append(hit['_source'])
    return candidates


def _count_candidates(client, index, groups, candidates, timeout):
    """{candidate key: facilities matching with it added}."""
    aggs = {}
    for i, candidate in enumerate(candidates):
        kind = KINDS_BY_NAME[candidate['kind']]
        clauses = flatten(groups, without={kind.group})
        value = candidate['value']
        if kind.kind == 'contributor':
            value = int(value)
        clauses.append({'term': {kind.field: value}})
        aggs[f'c{i}'] = {'filter': {'bool': {'filter': clauses}}}
    if not aggs:
        return {}
    response = client.search(index=index, body={
        'size': 0, 'track_total_hits': False, 'aggs': aggs,
    }, request_timeout=timeout)
    return {i: response['aggregations'][f'c{i}']['doc_count']
            for i in range(len(candidates))}


def _value_suggestions(client, index, q, groups, kinds,
                       hidden_contributors, timeout) -> List[dict]:
    candidates = [
        c for c in _value_candidates(client, q, kinds, timeout)
        if not (c['kind'] == 'contributor'
                and int(c['value']) in hidden_contributors)
    ]
    counts = _count_candidates(client, index, groups, candidates, timeout)
    suggestions = []
    for i, candidate in enumerate(candidates):
        if counts.get(i):
            kind = KINDS_BY_NAME[candidate['kind']]
            suggestions.append({
                'kind': kind.kind,
                'param': kind.param,
                'value': candidate['value'],
                'label': candidate['label'],
                'count': counts[i],
            })
    order = {kind: i for i, kind in enumerate(kinds)}
    return sorted(suggestions, key=lambda s: (order[s['kind']], -s['count']))


def suggest(client, index: str, params, q: str, timeout: float,
            kinds: Iterable[str] = DEFAULT_KINDS,
            hidden_contributors=frozenset()) -> Dict:
    """Raises UnsupportedQuery like the rest of the query builder."""
    kinds = [k for k in kinds if k in KINDS_BY_NAME]
    groups = build_filter_groups(params)
    return {
        'q': q,
        'os_id': (q.upper() if string_matches_os_id_format(q.upper())
                  else None),
        'facilities': _facility_suggestions(
            client, index, q, flatten(groups), timeout),
        'filters': _value_suggestions(
            client, index, q, groups, kinds, hidden_contributors, timeout),
    }
