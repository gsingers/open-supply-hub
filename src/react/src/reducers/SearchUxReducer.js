import { createReducer } from 'redux-act';
import update from 'immutability-helper';

import {
    startFetchFacets,
    completeFetchFacets,
    failFetchFacets,
    completeFetchFacetValues,
    completeFetchSingleFacet,
    toggleFacetOpen,
    toggleFacetExpanded,
    startFetchSuggestions,
    completeFetchSuggestions,
    failFetchSuggestions,
    clearSuggestions,
} from '../actions/searchUx';

const initialState = Object.freeze({
    facets: Object.freeze({
        data: null,
        fetching: false,
        // The server could not count (OpenSearch down or an unsupported
        // filter): the panel shows values without counts.
        unavailable: false,
        qs: '',
    }),
    // Groups the user opened or closed (others use their defaults).
    open: Object.freeze({}),
    expanded: Object.freeze({}),
    // Larger value lists for expanded facets, keyed by facet.
    expandedValues: Object.freeze({}),
    suggest: Object.freeze({
        q: '',
        data: null,
        fetching: false,
    }),
});

export default createReducer(
    {
        [startFetchFacets]: state =>
            update(state, { facets: { fetching: { $set: true } } }),
        [completeFetchFacets]: (state, { data, qs }) =>
            update(state, {
                facets: {
                    $set: {
                        data: data.available ? data : null,
                        fetching: false,
                        unavailable: !data.available,
                        qs,
                    },
                },
            }),
        [failFetchFacets]: state =>
            update(state, {
                facets: {
                    fetching: { $set: false },
                    unavailable: { $set: true },
                    data: { $set: null },
                },
            }),
        [completeFetchFacetValues]: (state, { key, data }) =>
            data.available && data.facets && data.facets[key]
                ? update(state, {
                      expandedValues: { [key]: { $set: data.facets[key] } },
                  })
                : state,
        [completeFetchSingleFacet]: (state, { key, data }) =>
            data.available &&
            data.facets &&
            data.facets[key] &&
            state.facets.data
                ? update(state, {
                      facets: {
                          data: { facets: { [key]: { $set: data.facets[key] } } },
                      },
                  })
                : state,
        [toggleFacetOpen]: (state, { key, open }) =>
            update(state, { open: { [key]: { $set: open } } }),
        [toggleFacetExpanded]: (state, { key, expanded }) =>
            update(state, { expanded: { [key]: { $set: expanded } } }),
        [startFetchSuggestions]: (state, q) =>
            update(state, {
                suggest: { q: { $set: q }, fetching: { $set: true } },
            }),
        [completeFetchSuggestions]: (state, data) =>
            update(state, {
                suggest: {
                    data: { $set: data.available ? data : null },
                    fetching: { $set: false },
                },
            }),
        [failFetchSuggestions]: state =>
            update(state, {
                suggest: { data: { $set: null }, fetching: { $set: false } },
            }),
        [clearSuggestions]: state =>
            update(state, { suggest: { $set: initialState.suggest } }),
    },
    initialState,
);
