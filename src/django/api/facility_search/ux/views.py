"""
GET /api/map-search/facets/  and  GET /api/map-search/suggest/

Both take the same filter params as /api/facilities/. They are cheap,
public and fired per keystroke or filter change, so they have their own
throttle scope, cap every size, and are cached for a short time in
memcached and (through Cache-Control) at CloudFront.

When the search UX is off they answer 404; when OpenSearch fails they
answer 503 and the client falls back to plain filters without counts.
"""
import hashlib
import logging

from django.conf import settings
from django.core.cache import caches
from rest_framework import status
from rest_framework.decorators import (
    api_view,
    permission_classes,
    throttle_classes,
)
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from api.facility_search import backend
from api.facility_search.index_definition import read_alias
from api.facility_search.query import UnsupportedQuery
from api.facility_search.ux import facets as facet_counts
from api.facility_search.ux import suggest as typeahead
from api.services.contributor_masking_policy import ContributorMaskingPolicy
from api.throttles import SearchUxThrottle
from api.views.contributor.active_contributors import active_contributors

log = logging.getLogger(__name__)

ACTIVE_CONTRIBUTORS_KEY = 'search_ux_active_contributor_ids'
ACTIVE_CONTRIBUTORS_SECONDS = 600


def _cache():
    return caches['view_cache']


def _cache_key(request, name: str, is_token: bool) -> str:
    query = request.query_params.urlencode()
    digest = hashlib.sha256(query.encode()).hexdigest()
    return f'search_ux:{name}:{int(is_token)}:{digest}'


def _is_token_request(request) -> bool:
    return bool(getattr(request, 'auth', None))


def _hidden_contributors(request) -> set:
    """Contributors the paid API masks, for token callers only."""
    if not _is_token_request(request):
        return set()
    return set(ContributorMaskingPolicy.flagged_contributors()
               .contributor_ids)


def _allowed_contributors(request) -> list:
    """Contributors the public list shows, minus masked ones."""
    ids = _cache().get(ACTIVE_CONTRIBUTORS_KEY)
    if ids is None:
        ids = sorted(active_contributors().values_list('id', flat=True))
        _cache().set(ACTIVE_CONTRIBUTORS_KEY, ids,
                     ACTIVE_CONTRIBUTORS_SECONDS)
    hidden = _hidden_contributors(request)
    return [i for i in ids if i not in hidden]


def _respond(data, cache_key=None):
    seconds = settings.MAP_SEARCH_UX_CACHE_SECONDS
    if cache_key:
        _cache().set(cache_key, data, seconds)
    response = Response(data)
    response['Cache-Control'] = f'public, max-age={seconds}'
    return response


def _cached_or(request, name, compute):
    """Serve from cache, or compute; maps failures to client fallbacks."""
    if not backend.ux_switched_on():
        return Response(status=status.HTTP_404_NOT_FOUND)
    key = _cache_key(request, name, _is_token_request(request))
    cached = _cache().get(key)
    if cached is not None:
        return _respond(cached)
    if not backend.breaker.allow():
        return Response({'available': False},
                        status=status.HTTP_503_SERVICE_UNAVAILABLE)
    try:
        data = compute()
    except UnsupportedQuery as reason:
        return _respond({'available': False, 'reason': str(reason)}, key)
    except Exception as error:
        backend.breaker.record_failure()
        log.warning('[Search UX] %s failed: %s', name, error)
        return Response({'available': False},
                        status=status.HTTP_503_SERVICE_UNAVAILABLE)
    backend.breaker.record_success()
    return _respond({'available': True, **data}, key)


def _facet_size(request) -> int:
    try:
        size = int(request.query_params.get('facet_size',
                                            facet_counts.DEFAULT_SIZE))
    except ValueError:
        size = facet_counts.DEFAULT_SIZE
    return max(1, min(size, facet_counts.MAX_SIZE))


@api_view(['GET'])
@permission_classes((AllowAny,))
@throttle_classes([SearchUxThrottle])
def map_search_facets(request):
    """
    Facet counts for the current filters: `facet` (repeatable, default
    all), `facet_size` (1-50), `sort_by`, plus every /api/facilities/
    filter param. Returns `total`, `facets`, `relaxations` (the count
    without each active filter) and `did_you_mean`.
    """
    params = request.query_params

    def compute():
        facets = facet_counts.requested_facets(
            params.getlist('facet'), embed='embed' in params)
        template = facet_counts.template_params(
            params, params.get('sort_by'), facets, _facet_size(request),
            _allowed_contributors(request))
        return facet_counts.run(backend.client(), read_alias(), template,
                                facets, backend.request_timeout())

    return _cached_or(request, 'facets', compute)


@api_view(['GET'])
@permission_classes((AllowAny,))
@throttle_classes([SearchUxThrottle])
def map_search_suggest(request):
    """
    Typeahead for `q` under the current filters: `facilities` (up to 5,
    one per distinct name), `filters` (values to add as filter chips,
    each with the count it would give) and `os_id` when `q` is one.
    """
    params = request.query_params
    q = typeahead.normalize_query(params.get('q'))
    if q is None:
        return Response({'available': True, 'q': params.get('q', ''),
                         'os_id': None, 'facilities': [], 'filters': []})
    # The typed text is the suggestion query, not a committed filter.
    filters = params.copy()
    filters.pop('q', None)

    def compute():
        return typeahead.suggest(
            backend.client(), read_alias(), filters, q,
            backend.request_timeout(),
            hidden_contributors=_hidden_contributors(request))

    return _cached_or(request, 'suggest', compute)
