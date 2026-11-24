"""Template parameters shared by the scored list and the facet counts."""
import re
from typing import Dict, List, Optional

from unidecode import unidecode

from api.constants import FacilitiesQueryParams as P
from api.facility_search.query import (
    TEXT_GROUP,
    build_filter_groups,
    text_match_clause,
)
from api.os_id import string_matches_os_id_format

# Longest text sent to the scored query; longer input is cut, not refused.
MAX_TEXT_LENGTH = 200

NON_ALPHANUMERIC = re.compile(r'[^0-9a-z]')

# The legacy substring match stays in the scored query as a constant,
# low-scoring clause, so everything the legacy search found is still
# found, ranked below real word matches.
FALLBACK_BOOST = 0.3


def scored_text(params) -> Optional[str]:
    """The `q` to score, or None when there is none (or it is an OS ID)."""
    text = (params.get(P.Q) or '').strip()[:MAX_TEXT_LENGTH]
    if not text or string_matches_os_id_format(text):
        return None
    return text


def sort_key(text: str) -> str:
    """Approximates the `collation_key` normalizer of `name.sort`."""
    return NON_ALPHANUMERIC.sub('', unidecode(text).lower())


def text_params(params, text: Optional[str]) -> dict:
    """The template parameters that describe the text part of a search."""
    if text is None:
        return {'has_text': False}
    fallback = text_match_clause(params, text)
    return {
        'has_text': True,
        'q': text,
        'text_fallback': {'constant_score': {
            'filter': fallback, 'boost': FALLBACK_BOOST,
        }},
    }


def filter_groups(params, text: Optional[str]) -> Dict[str, List[dict]]:
    """
    Filter groups with the text left out when it is scored instead.
    Raises UnsupportedQuery like the rest of the query builder.
    """
    return build_filter_groups(params, include_text=text is None)


def without_text(groups: Dict[str, List[dict]]) -> Dict[str, List[dict]]:
    return {k: v for k, v in groups.items() if k != TEXT_GROUP}
