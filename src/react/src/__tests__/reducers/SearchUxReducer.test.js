import reducer from '../../reducers/SearchUxReducer';
import {
    completeFetchFacets,
    completeFetchFacetValues,
    failFetchFacets,
    startFetchFacets,
    toggleFacetExpanded,
} from '../../actions/searchUx';

const initial = reducer(undefined, { type: '@@INIT' });

describe('SearchUxReducer', () => {
    it('stores counts, and marks them unavailable when the server cannot count', () => {
        const fetching = reducer(initial, startFetchFacets());
        expect(fetching.facets.fetching).toBe(true);

        const done = reducer(
            fetching,
            completeFetchFacets({ data: { available: true, total: 3 }, qs: 'a' }),
        );
        expect(done.facets.data.total).toBe(3);
        expect(done.facets.unavailable).toBe(false);

        const unsupported = reducer(
            done,
            completeFetchFacets({ data: { available: false }, qs: 'b' }),
        );
        expect(unsupported.facets.data).toBeNull();
        expect(unsupported.facets.unavailable).toBe(true);

        expect(reducer(done, failFetchFacets()).facets.unavailable).toBe(true);
    });

    it('keeps expanded value lists per facet', () => {
        const expanded = reducer(
            initial,
            toggleFacetExpanded({ key: 'countries', expanded: true }),
        );
        const values = reducer(
            expanded,
            completeFetchFacetValues({
                key: 'countries',
                data: { available: true, facets: { countries: { values: [] } } },
            }),
        );
        expect(values.expanded.countries).toBe(true);
        expect(values.expandedValues.countries).toEqual({ values: [] });
    });
});
