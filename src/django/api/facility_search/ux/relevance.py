"""
The scored ("Best match") list search: `sort_by=relevance` with a `q`.

It widens matching from the legacy name substring to words in the name,
native name, parent company, address and product type (fuzzy, with
synonyms), keeps every legacy substring match as a low-scoring fallback,
and returns highlights. Without a `q` there is nothing to score, so the
caller keeps the legacy path and its default order.
"""
from typing import Dict, List, Optional

from api.facility_search.query import flatten
from api.facility_search.ux import assets
from api.facility_search.ux.text import filter_groups, scored_text, text_params

RELEVANCE = 'relevance'
SCORED_SORT = [
    '_score',
    {'contributors_count': 'desc'},
    {'os_id': 'asc'},
]


def wants_relevance(params, sort_by: Optional[str]) -> bool:
    return sort_by == RELEVANCE and scored_text(params) is not None


def list_template_params(params, offset: int, size: int,
                         aggs: dict) -> dict:
    """Parameters for the search template. Raises UnsupportedQuery."""
    text = scored_text(params)
    return {
        'from': offset,
        'size': size,
        'filters': flatten(filter_groups(params, text)),
        'sort': SCORED_SORT,
        'aggs': aggs,
        'highlight': True,
        'suggest': False,
        **text_params(params, text),
    }


def parse_highlights(hits: List[dict]) -> Dict[str, Dict[str, str]]:
    """{os_id: {field: first fragment}} for the hits that have one."""
    highlights = {}
    for hit in hits:
        fields = {
            field.split('.')[0].replace('_text', ''): fragments[0]
            for field, fragments in (hit.get('highlight') or {}).items()
            if fragments
        }
        if fields:
            highlights[hit['_id']] = fields
    return highlights


def execute_template(client, index: str, template_params: dict,
                     timeout: float) -> dict:
    return assets.search_template(
        client, index, assets.search_template_id(), template_params,
        assets.search_pipeline_id(), timeout,
    )
