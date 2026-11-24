// Search UX on /map (facets, typeahead, scored search, instant search).
// Server side: src/django/api/facility_search/ux.
import isEmpty from 'lodash/isEmpty';
import querystring from './qs';

import {
    updateContributorFilter,
    updateContributorTypeFilter,
    updateCountryFilter,
    updateSectorFilter,
    updateParentCompanyFilter,
    updateFacilityTypeFilter,
    updateProcessingTypeFilter,
    updateProductTypeFilter,
    updateNumberofWorkersFilter,
    updateClaimStatusFacetFilter,
    updateListFilter,
    updateBoundaryFilter,
    updateIsic4Filter,
    updateIsic5Filter,
    updateNativeLanguageNameFilter,
    updateFacilityFreeTextQueryFilter,
} from '../actions/filters';

import { createQueryStringFromSearchFilters } from './util';

export const SEARCH_UX_FLAG = 'map_search_ux';

export const RELEVANCE_SORT = 'relevance';

export const FACETS_URL = '/api/map-search/facets/';
export const SUGGEST_URL = '/api/map-search/suggest/';

// Debounce before a filter change runs the search, and before a
// keystroke asks for suggestions.
export const INSTANT_SEARCH_DELAY_MS = 250;
export const SUGGEST_DELAY_MS = 150;

export const FACET_PAGE_SIZE = 10;
export const FACET_EXPANDED_SIZE = 50;

// Private-use characters the server wraps highlighted text in
// (ux/assets.py HIGHLIGHT_PRE / HIGHLIGHT_POST).
export const HIGHLIGHT_PRE = '';
export const HIGHLIGHT_POST = '';

// Facets in the order the panel shows them. `filterKey` is the redux
// filters field; `update` the action that sets it; `embedField` the
// embed config extended field that must be on for it to show in embed
// mode (null: always shown, false: never shown in embed mode).
export const FACETS = Object.freeze([
    {
        key: 'countries',
        label: 'Country',
        filterKey: 'countries',
        update: updateCountryFilter,
        embedField: null,
        open: true,
    },
    {
        key: 'sectors',
        label: 'Sector',
        filterKey: 'sectors',
        update: updateSectorFilter,
        embedField: null,
        open: true,
    },
    {
        key: 'contributors',
        label: 'Data contributor',
        filterKey: 'contributors',
        update: updateContributorFilter,
        embedField: false,
        open: false,
    },
    {
        key: 'contributor_types',
        label: 'Data contributor type',
        filterKey: 'contributorTypes',
        update: updateContributorTypeFilter,
        embedField: false,
        open: false,
    },
    {
        key: 'claim_status',
        label: 'Claim status',
        filterKey: 'claimStatus',
        update: updateClaimStatusFacetFilter,
        embedField: null,
        open: false,
    },
    {
        key: 'facility_type',
        label: 'Location type',
        filterKey: 'facilityType',
        update: updateFacilityTypeFilter,
        embedField: 'facility_type',
        open: false,
    },
    {
        key: 'processing_type',
        label: 'Processing type',
        filterKey: 'processingType',
        update: updateProcessingTypeFilter,
        embedField: 'processing_type',
        open: false,
    },
    {
        key: 'product_type',
        label: 'Product type',
        filterKey: 'productType',
        update: updateProductTypeFilter,
        embedField: 'product_type',
        open: false,
    },
    {
        key: 'number_of_workers',
        label: 'Number of workers',
        filterKey: 'numberOfWorkers',
        update: updateNumberofWorkersFilter,
        embedField: 'number_of_workers',
        open: false,
    },
    {
        key: 'parent_company',
        label: 'Parent company',
        filterKey: 'parentCompany',
        update: updateParentCompanyFilter,
        embedField: 'parent_company',
        open: false,
    },
]);

export const FACETS_BY_KEY = Object.freeze(
    FACETS.reduce((acc, facet) => ({ ...acc, [facet.key]: facet }), {}),
);

// Typeahead value kinds (ux/values.py) to the facet they filter.
export const SUGGESTION_KIND_FACET = Object.freeze({
    country: 'countries',
    contributor: 'contributors',
    parent_company: 'parent_company',
    sector: 'sectors',
    product_type: 'product_type',
    facility_type: 'facility_type',
    processing_type: 'processing_type',
    contributor_type: 'contributor_types',
});

// Filter groups (query.build_filter_groups) to what clears them, for
// "remove this filter" suggestions on empty results.
export const GROUP_CLEARERS = Object.freeze({
    text: [{ action: updateFacilityFreeTextQueryFilter, value: '' }],
    countries: [{ action: updateCountryFilter, value: [] }],
    sectors: [{ action: updateSectorFilter, value: [] }],
    contributors: [{ action: updateContributorFilter, value: [] }],
    contributor_types: [{ action: updateContributorTypeFilter, value: [] }],
    lists: [{ action: updateListFilter, value: [] }],
    boundary: [{ action: updateBoundaryFilter, value: null }],
    parent_company: [{ action: updateParentCompanyFilter, value: [] }],
    facility_processing: [
        { action: updateFacilityTypeFilter, value: [] },
        { action: updateProcessingTypeFilter, value: [] },
    ],
    isic: [
        { action: updateIsic4Filter, value: [] },
        { action: updateIsic5Filter, value: [] },
    ],
    product_type: [{ action: updateProductTypeFilter, value: [] }],
    number_of_workers: [{ action: updateNumberofWorkersFilter, value: [] }],
    native_language_name: [
        { action: updateNativeLanguageNameFilter, value: '' },
    ],
    claim_status: [{ action: updateClaimStatusFacetFilter, value: [] }],
});

export const GROUP_LABELS = Object.freeze({
    text: 'Search text',
    lists: 'List',
    boundary: 'Drawn area',
    facility_processing: 'Location / processing type',
    isic: 'ISIC code',
    native_language_name: 'Native language name',
    ...FACETS.reduce(
        (acc, facet) => ({ ...acc, [facet.key]: facet.label }),
        {},
    ),
});

export const CLAIM_STATUS_LABELS = Object.freeze({
    claimed: 'Claimed',
    unclaimed: 'Not claimed',
});

export const isSearchUxActive = flags => !!(flags && flags[SEARCH_UX_FLAG]);

// Facets to show: all outside embed mode; in embed mode only those the
// embed config turns on, and never the (pinned) contributor ones.
export const visibleFacets = (embed, extendedFields = []) =>
    FACETS.filter(facet => {
        if (!embed) return true;
        if (facet.embedField === null) return true;
        if (facet.embedField === false) return false;
        return extendedFields.includes(facet.embedField);
    });

// A group is open if the user opened it, or by default when it starts
// open or has selections. Only open groups are counted: each facet costs
// a terms aggregation over the whole result set.
export const isFacetOpen = (openState, facet, filters) => {
    if (openState && openState[facet.key] !== undefined) {
        return openState[facet.key];
    }
    return facet.open || ((filters && filters[facet.filterKey]) || []).length > 0;
};

export const searchQueryString = (filters, embed, extra = {}) => {
    const base = createQueryStringFromSearchFilters(filters, embed);
    const more = querystring.stringify(
        Object.fromEntries(
            Object.entries(extra).filter(([, value]) => !isEmpty(value)),
        ),
    );
    return [base, more].filter(Boolean).join('&');
};

// [{ text, hit }] pieces of a server-highlighted string. Rendered as text
// nodes, so the underlying value can never inject markup.
export const splitHighlight = highlighted => {
    if (!highlighted) return [];
    const pieces = [];
    let rest = highlighted;
    while (rest.length) {
        const start = rest.indexOf(HIGHLIGHT_PRE);
        if (start === -1) {
            pieces.push({ text: rest, hit: false });
            break;
        }
        if (start > 0) {
            pieces.push({ text: rest.slice(0, start), hit: false });
        }
        const end = rest.indexOf(HIGHLIGHT_POST, start);
        const stop = end === -1 ? rest.length : end;
        pieces.push({ text: rest.slice(start + 1, stop), hit: true });
        rest = end === -1 ? '' : rest.slice(end + 1);
    }
    return pieces;
};

export const stripHighlight = highlighted =>
    splitHighlight(highlighted)
        .map(piece => piece.text)
        .join('');

// The label a facet value shows: option lists for coded values (country
// codes, contributor ids), otherwise the value itself.
export const facetValueLabel = (facetKey, value, labelMaps) => {
    if (facetKey === 'claim_status') {
        return CLAIM_STATUS_LABELS[value] || value;
    }
    const map = labelMaps[facetKey];
    if (map && map[value] !== undefined) {
        return map[value];
    }
    return String(value);
};

export const optionsToLabelMap = options =>
    (options || []).reduce(
        (acc, option) => ({ ...acc, [option.value]: option.label }),
        {},
    );

const sameValue = (a, b) => String(a) === String(b);

export const isSelected = (selected, value) =>
    (selected || []).some(option => sameValue(option.value, value));

export const toggleOption = (selected, value, label) =>
    isSelected(selected, value)
        ? selected.filter(option => !sameValue(option.value, value))
        : [...(selected || []), { value, label }];
