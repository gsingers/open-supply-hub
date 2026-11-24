import { useEffect, useMemo, useRef } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import debounce from 'lodash/debounce';
import get from 'lodash/get';

import { fetchFacets, runInstantSearch } from '../../actions/searchUx';
import { VECTOR_TILE } from '../../util/constants';
import {
    INSTANT_SEARCH_DELAY_MS,
    searchQueryString,
} from '../../util/searchUx';

// Runs the search whenever the filters change (debounced), replacing the
// Search button. Skips the first render: the page load already fetched
// the list for the URL's filters, so only the facet counts are needed.
export default function useInstantSearch(enabled) {
    const dispatch = useDispatch();
    const filters = useSelector(state => state.filters);
    const embed = useSelector(state => state.embeddedMap.embed);
    const vectorTilesAreActive = useSelector(state =>
        get(state, ['featureFlags', 'flags', VECTOR_TILE], false),
    );
    const qs = searchQueryString(filters, embed);
    const lastRun = useRef(null);

    const run = useMemo(
        () =>
            debounce(
                () => dispatch(runInstantSearch({ vectorTilesAreActive })),
                INSTANT_SEARCH_DELAY_MS,
            ),
        [dispatch, vectorTilesAreActive],
    );
    useEffect(() => () => run.cancel(), [run]);

    useEffect(() => {
        if (!enabled) return;
        if (lastRun.current === null) {
            lastRun.current = qs;
            // Counts for the filters the page was opened with.
            dispatch(fetchFacets());
            return;
        }
        if (lastRun.current !== qs) {
            lastRun.current = qs;
            run();
        }
    }, [enabled, qs, run, dispatch, vectorTilesAreActive]);
}
