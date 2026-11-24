"""
Search templates and search pipelines for the search UX, stored in the
cluster by `manage.py install_map_search_ux`.

Templates hold the query shape and every relevance decision (fields,
boosts, fuzziness, phrase and prefix bonuses, the popularity signal).
Pipelines hold what OpenSearch 2.15 pipelines can do around a query:

* `SEARCH_PIPELINE` caps `size` and `from` in the cluster, a second line
  of defence behind the view's own limits, and is the place where a
  future rerank or hybrid-normalization processor plugs in.
* `SUGGEST_PIPELINE` oversamples, collapses hits that share a name (chains
  with dozens of same-named sites) and truncates back to the asked size.

Both names and template ids carry a version. A relevance change installs
a new version next to the old one and the setting
`MAP_SEARCH_UX_ASSET_VERSION` picks which one Django uses.

Values are interpolated with `{{x}}` inside JSON strings (OpenSearch's
mustache escapes them as JSON) and with `{{#toJson}}x{{/toJson}}` for
objects and arrays. Numbers are always passed, never defaulted: mustache
treats 0 as false.
"""
import json

from django.conf import settings

# Private-use characters around highlighted text. The client splits on
# them and renders plain text nodes, so a facility name can never inject
# markup through a highlight.
HIGHLIGHT_PRE = ''
HIGHLIGHT_POST = ''

# Text fields searched by the scored query, with their boosts.
SEARCH_FIELDS = [
    'name_text^4',
    'native_language_name.text^3',
    'parent_company_name.text^2',
    'address_text^1',
    'product_type.text^1',
]

HIGHLIGHT_FIELDS = {
    'name_text': {'number_of_fragments': 0},
    'native_language_name.text': {'number_of_fragments': 0},
    'parent_company_name.text': {'number_of_fragments': 0},
    'address_text': {'number_of_fragments': 1, 'fragment_size': 120},
    'product_type.text': {'number_of_fragments': 0},
}

# The scored text clause shared by the list and the facet counts, so a
# facet count always describes the same result set the list shows.
_TEXT_CLAUSE = '''
{"bool": {"minimum_should_match": 1, "should": [
  {"multi_match": {"query": "{{q}}", "type": "best_fields",
    "operator": "and", "fields": %(fields)s, "tie_breaker": 0.2,
    "fuzziness": "AUTO", "prefix_length": 1, "max_expansions": 20}},
  {"match_phrase": {"name_text": {"query": "{{q}}", "slop": 1,
    "boost": 6}}},
  {"match": {"name_text.prefix": {"query": "{{q}}", "operator": "and",
    "analyzer": "folded_text", "boost": 0.5}}},
  {{#toJson}}text_fallback{{/toJson}}
]}}
''' % {'fields': json.dumps(SEARCH_FIELDS)}

# Without text there is nothing to rank (facet counts only), so `must` is
# match_all and the popularity clause is left out: a bool whose only
# clauses are `should` would have to match one of them.
SEARCH_TEMPLATE_SOURCE = '''{
  "from": {{from}},
  "size": {{size}},
  "track_total_hits": true,
  "_source": false,
  "query": {"bool": {
    "filter": {{#toJson}}filters{{/toJson}},
    "must": [ {{#has_text}}%(text)s{{/has_text}}
              {{^has_text}}{"match_all": {}}{{/has_text}} ],
    "should": [ {{#has_text}}
      {"rank_feature": {"field": "popularity",
        "saturation": {"pivot": 3}, "boost": 1.5}}
    {{/has_text}} ],
    "minimum_should_match": 0
  }},
  "sort": {{#toJson}}sort{{/toJson}},
  "aggs": {{#toJson}}aggs{{/toJson}}
  {{#highlight}},
  "highlight": {
    "pre_tags": ["%(pre)s"], "post_tags": ["%(post)s"],
    "require_field_match": true,
    "fields": %(highlight_fields)s
  }
  {{/highlight}}
  {{#suggest}},
  "suggest": {"text": "{{q}}", "did_you_mean": {"phrase": {
    "field": "name_text", "analyzer": "folded_text", "size": 1,
    "gram_size": 1, "max_errors": 2, "confidence": 1.0,
    "direct_generator": [{"field": "name_text", "suggest_mode": "always",
                          "min_word_length": 3}],
    "highlight": {"pre_tag": "%(pre)s", "post_tag": "%(post)s"}
  }}}
  {{/suggest}}
}''' % {
    'text': _TEXT_CLAUSE,
    'pre': HIGHLIGHT_PRE,
    'post': HIGHLIGHT_POST,
    'highlight_fields': json.dumps(HIGHLIGHT_FIELDS),
}

SUGGEST_TEMPLATE_SOURCE = '''{
  "size": {{size}},
  "_source": ["os_id", "name", "country_code", "contributors_count"],
  "query": {"bool": {
    "filter": {{#toJson}}filters{{/toJson}},
    "must": [{"bool": {"minimum_should_match": 1, "should": [
      {"match": {"name_text.prefix": {"query": "{{q}}",
        "operator": "and", "analyzer": "folded_text"}}},
      {"match": {"native_language_name.text": {"query": "{{q}}",
        "operator": "and"}}}
    ]}}],
    "should": [
      {"prefix": {"name.sort": {"value": "{{q_key}}", "boost": 4}}},
      {"match_phrase": {"name_text": {"query": "{{q}}", "boost": 2}}},
      {"rank_feature": {"field": "popularity",
        "saturation": {"pivot": 3}, "boost": 3}}
    ],
    "minimum_should_match": 0
  }},
  "sort": ["_score", {"contributors_count": "desc"}, {"os_id": "asc"}]
}'''

VALUES_TEMPLATE_SOURCE = '''{
  "size": 0,
  "query": {"bool": {
    "filter": [{"terms": {"kind": {{#toJson}}kinds{{/toJson}}}}],
    "must": [{"match": {"label_text.prefix": {"query": "{{q}}",
      "operator": "and", "analyzer": "folded_text"}}}],
    "should": [
      {"prefix": {"label_key": {"value": "{{q_key}}", "boost": 3}}},
      {"rank_feature": {"field": "doc_count",
        "saturation": {"pivot": 50}, "boost": 2}}
    ],
    "minimum_should_match": 0
  }},
  "aggs": {"kinds": {"terms": {"field": "kind", "size": 20},
    "aggs": {"top": {"top_hits": {"size": {{per_kind}},
      "_source": ["kind", "value", "label"]}}}}}
}'''

# Requests whose size or depth exceed these are clamped in the cluster.
MAX_PAGE_SIZE = 100

SEARCH_PIPELINE_BODY = {
    'description': 'Search UX list and facet requests',
    'request_processors': [{
        'script': {
            'tag': 'clamp_page',
            # Depth is already bounded by index.max_result_window.
            'description': 'Cap page size (scraping guard)',
            'lang': 'painless',
            'source': (
                f'if (ctx._source.size > {MAX_PAGE_SIZE}) '
                f'{{ ctx._source.size = {MAX_PAGE_SIZE}; }}'
            ),
        },
    }],
}

SUGGEST_PIPELINE_BODY = {
    'description': 'Typeahead: one suggestion per distinct name',
    'request_processors': [{'oversample': {'sample_factor': 4}}],
    'response_processors': [
        {'collapse': {'field': 'name'}},
        {'truncate_hits': {}},
    ],
}


def _version():
    return settings.MAP_SEARCH_UX_ASSET_VERSION


def search_template_id():
    return f'facilities-ux-search-v{_version()}'


def suggest_template_id():
    return f'facilities-ux-suggest-v{_version()}'


def values_template_id():
    return f'facilities-ux-values-v{_version()}'


def search_pipeline_id():
    return f'facilities-ux-search-v{_version()}'


def suggest_pipeline_id():
    return f'facilities-ux-suggest-v{_version()}'


def install(client) -> list:
    """Store every template and pipeline; returns what was installed."""
    templates = {
        search_template_id(): SEARCH_TEMPLATE_SOURCE,
        suggest_template_id(): SUGGEST_TEMPLATE_SOURCE,
        values_template_id(): VALUES_TEMPLATE_SOURCE,
    }
    for template_id, source in templates.items():
        client.put_script(id=template_id, body={
            'script': {'lang': 'mustache', 'source': source},
        })
    pipelines = {
        search_pipeline_id(): SEARCH_PIPELINE_BODY,
        suggest_pipeline_id(): SUGGEST_PIPELINE_BODY,
    }
    for pipeline_id, body in pipelines.items():
        client.transport.perform_request(
            'PUT', f'/_search/pipeline/{pipeline_id}', body=body,
        )
    return sorted(templates) + sorted(pipelines)


def search_template(client, index, template_id, params, pipeline,
                    timeout):
    """Run a stored template through a search pipeline."""
    return client.search_template(
        index=index,
        body={'id': template_id, 'params': params},
        params={'search_pipeline': pipeline},
        request_timeout=timeout,
    )
