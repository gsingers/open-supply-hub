import { createAction } from 'redux-act';
import noop from 'lodash/noop';

import apiRequest from '../util/apiRequest';
import { fetchFacilities } from './facilities';
import { updateFacilityFreeTextQueryFilter, updateSortAlgorithm } from './filters';
import {
    DEFAULT_SORT_OPTION_INDEX,
    FACILITIES_REQUEST_PAGE_SIZE,
    optionsForSortingResults,
} from '../util/constants';
import {
    FACETS_BY_KEY,
    FACETS_URL,
    FACET_EXPANDED_SIZE,
    FACET_PAGE_SIZE,
    GROUP_CLEARERS,
    RELEVANCE_SORT,
    SUGGESTION_KIND_FACET,
    SUGGEST_URL,
    isFacetOpen,
    isSelected,
    searchQueryString,
    toggleOption,
    visibleFacets,
} from '../util/searchUx';

export const startFetchFacets = createAction('START_FETCH_SEARCH_UX_FACETS');
export const completeFetchFacets = createAction(
    'COMPLETE_FETCH_SEARCH_UX_FACETS',
);
export const failFetchFacets = createAction('FAIL_FETCH_SEARCH_UX_FACETS');
export const completeFetchFacetValues = createAction(
    'COMPLETE_FETCH_SEARCH_UX_FACET_VALUES',
);
export const toggleFacetOpen = createAction('TOGGLE_SEARCH_UX_FACET_OPEN');
export const completeFetchSingleFacet = createAction(
    'COMPLETE_FETCH_SEARCH_UX_SINGLE_FACET',
);
export const toggleFacetExpanded = createAction(
    'TOGGLE_SEARCH_UX_FACET_EXPANDED',
);
export const startFetchSuggestions = createAction(
    'START_FETCH_SEARCH_UX_SUGGESTIONS',
);
export const completeFetchSuggestions = createAction(
    'COMPLETE_FETCH_SEARCH_UX_SUGGESTIONS',
);
export const failFetchSuggestions = createAction(
    'FAIL_FETCH_SEARCH_UX_SUGGESTIONS',
);
export const clearSuggestions = createAction('CLEAR_SEARCH_UX_SUGGESTIONS');

const relevanceOption = () =>
    optionsForSortingResults.find(option => option.value === RELEVANCE_SORT);
const defaultSortOption = () =>
    optionsForSortingResults[DEFAULT_SORT_OPTION_INDEX];

// One in-flight request per kind; a newer one aborts the older, so a
// slow response can never overwrite a newer one.
const inFlight = {};
const startRequest = kind => {
    if (inFlight[kind]) {
        inFlight[kind].abort();
    }
    inFlight[kind] = new AbortController();
    return inFlight[kind].signal;
};
const isAbort = error =>
    error && (error.name === 'CanceledError' || error.code === 'ERR_CANCELED');

const facetKeysFor = ({ embeddedMap: { embed, config }, searchUx, filters }) =>
    visibleFacets(embed, (config && config.extended_fields) || [])
        .filter(facet => isFacetOpen(searchUx.open, facet, filters))
        .map(facet => facet.key);

function fetchExpandedFacet(key) {
    return (dispatch, getState) => {
        const state = getState();
        const qs = searchQueryString(state.filters, state.embeddedMap.embed, {
            facet: [key],
            facet_size: String(FACET_EXPANDED_SIZE),
        });
        return apiRequest
            .get(`${FACETS_URL}?${qs}`, { signal: startRequest(`f_${key}`) })
            .then(({ data }) =>
                dispatch(completeFetchFacetValues({ key, data })),
            )
            .catch(noop);
    };
}

export function fetchFacets() {
    return (dispatch, getState) => {
        const state = getState();
        const qs = searchQueryString(state.filters, state.embeddedMap.embed, {
            facet: facetKeysFor(state),
            facet_size: String(FACET_PAGE_SIZE),
        });
        dispatch(startFetchFacets());
        Object.keys(state.searchUx.expanded)
            .filter(key => state.searchUx.expanded[key])
            .forEach(key => dispatch(fetchExpandedFacet(key)));
        return apiRequest
            .get(`${FACETS_URL}?${qs}`, { signal: startRequest('facets') })
            .then(({ data }) => dispatch(completeFetchFacets({ data, qs })))
            .catch(error => {
                if (!isAbort(error)) {
                    // The panel falls back to values without counts.
                    dispatch(failFetchFacets());
                }
            });
    };
}

// Opening a collapsed group fetches its counts for the current filters.
export function openFacet(key, open) {
    return (dispatch, getState) => {
        dispatch(toggleFacetOpen({ key, open }));
        const state = getState();
        const {data} = state.searchUx.facets;
        if (!open || (data && data.facets && data.facets[key])) {
            return null;
        }
        const qs = searchQueryString(state.filters, state.embeddedMap.embed, {
            facet: [key],
            facet_size: String(FACET_PAGE_SIZE),
        });
        return apiRequest
            .get(`${FACETS_URL}?${qs}`, { signal: startRequest(`o_${key}`) })
            .then(({ data: response }) =>
                dispatch(completeFetchSingleFacet({ key, data: response })),
            )
            .catch(noop);
    };
}

export function expandFacet(key, expanded) {
    return dispatch => {
        dispatch(toggleFacetExpanded({ key, expanded }));
        return expanded ? dispatch(fetchExpandedFacet(key)) : null;
    };
}

export function fetchSuggestions(q) {
    return (dispatch, getState) => {
        const { filters, embeddedMap } = getState();
        const qs = searchQueryString(
            { ...filters, facilityFreeTextQuery: '' },
            embeddedMap.embed,
            { q },
        );
        dispatch(startFetchSuggestions(q));
        return apiRequest
            .get(`${SUGGEST_URL}?${qs}`, { signal: startRequest('suggest') })
            .then(({ data }) => dispatch(completeFetchSuggestions(data)))
            .catch(error => {
                if (!isAbort(error)) {
                    dispatch(failFetchSuggestions());
                }
            });
    };
}

// Runs the search for the current filters without leaving the page:
// list, count, extent (and so the map) plus the facet counts.
export function runInstantSearch({ vectorTilesAreActive }) {
    return dispatch => {
        dispatch(
            fetchFacilities({
                pageSize: vectorTilesAreActive
                    ? FACILITIES_REQUEST_PAGE_SIZE
                    : 50,
                // Narrowing to one result must not navigate away.
                pushNewRoute: noop,
                activateFacilitiesTab: false,
            }),
        );
        return dispatch(fetchFacets());
    };
}

// Commits typed text as the search. Text is ranked by best match by
// default; clearing it returns to the default order.
export function commitSearchText(text) {
    return (dispatch, getState) => {
        const trimmed = (text || '').trim();
        const sort = getState().filters.sortAlgorithm;
        const sortValue = sort && sort.value;
        dispatch(updateFacilityFreeTextQueryFilter(trimmed));
        if (trimmed && sortValue !== RELEVANCE_SORT) {
            dispatch(updateSortAlgorithm(relevanceOption()));
        } else if (!trimmed && sortValue === RELEVANCE_SORT) {
            dispatch(updateSortAlgorithm(defaultSortOption()));
        }
        dispatch(clearSuggestions());
    };
}

export function toggleFacetValue(facetKey, value, label) {
    return (dispatch, getState) => {
        const facet = FACETS_BY_KEY[facetKey];
        const selected = getState().filters[facet.filterKey];
        dispatch(facet.update(toggleOption(selected, value, label)));
    };
}

// A typeahead value becomes a filter chip, not free text.
export function applySuggestedFilter({ kind, value, label }) {
    return (dispatch, getState) => {
        const facet = FACETS_BY_KEY[SUGGESTION_KIND_FACET[kind]];
        if (!facet) return null;
        const typed = kind === 'contributor' ? Number(value) : value;
        const selected = getState().filters[facet.filterKey];
        dispatch(clearSuggestions());
        if (isSelected(selected, typed)) return null;
        return dispatch(
            facet.update([...(selected || []), { value: typed, label }]),
        );
    };
}

export function removeFilterGroup(group) {
    return dispatch =>
        (GROUP_CLEARERS[group] || []).forEach(({ action, value }) =>
            dispatch(action(value)),
        );
}
