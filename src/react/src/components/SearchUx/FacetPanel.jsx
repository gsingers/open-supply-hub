import React, { useMemo } from 'react';
import { arrayOf, func, shape, string } from 'prop-types';
import { connect } from 'react-redux';

import FacetGroup from './FacetGroup';
import {
    expandFacet,
    openFacet,
    toggleFacetValue,
} from '../../actions/searchUx';
import {
    isFacetOpen,
    optionsToLabelMap,
    visibleFacets,
} from '../../util/searchUx';

// Facet groups with live counts. Rendered only while counts are
// available; the search tab falls back to the plain filters otherwise.
function FacetPanel({
    facets,
    facetData,
    open,
    expanded,
    expandedValues,
    filters,
    countryOptions,
    contributorOptions,
    toggleValue,
    expand,
    setOpen,
}) {
    const labelMaps = useMemo(
        () => ({
            countries: optionsToLabelMap(countryOptions),
            contributors: optionsToLabelMap(contributorOptions),
        }),
        [countryOptions, contributorOptions],
    );

    return (
        <div>
            {facets.map(facet => (
                    <FacetGroup
                        key={facet.key}
                        facet={facet}
                        data={facetData[facet.key] || null}
                        expandedData={expandedValues[facet.key] || null}
                        expanded={!!expanded[facet.key]}
                        open={isFacetOpen(open, facet, filters)}
                        onOpen={value => setOpen(facet.key, value)}
                        selected={filters[facet.filterKey]}
                        labelMaps={labelMaps}
                        onToggleValue={(value, label) =>
                            toggleValue(facet.key, value, label)
                        }
                        onExpand={value => expand(facet.key, value)}
                    />
                ))}
        </div>
    );
}

FacetPanel.defaultProps = {
    countryOptions: [],
    contributorOptions: [],
};

FacetPanel.propTypes = {
    facets: arrayOf(shape({ key: string.isRequired })).isRequired,
    facetData: shape({}).isRequired,
    open: shape({}).isRequired,
    expanded: shape({}).isRequired,
    expandedValues: shape({}).isRequired,
    filters: shape({}).isRequired,
    countryOptions: arrayOf(shape({})),
    contributorOptions: arrayOf(shape({})),
    toggleValue: func.isRequired,
    expand: func.isRequired,
    setOpen: func.isRequired,
};

const mapStateToProps = ({
    searchUx: { facets, open, expanded, expandedValues },
    filters,
    filterOptions,
    embeddedMap: { embed, config },
}) => ({
    facets: visibleFacets(embed, (config && config.extended_fields) || []),
    facetData: (facets.data && facets.data.facets) || {},
    open,
    expanded,
    expandedValues,
    filters,
    countryOptions: filterOptions.countries.data,
    contributorOptions: filterOptions.contributors.data,
});

const mapDispatchToProps = dispatch => ({
    toggleValue: (key, value, label) =>
        dispatch(toggleFacetValue(key, value, label)),
    expand: (key, value) => dispatch(expandFacet(key, value)),
    setOpen: (key, value) => dispatch(openFacet(key, value)),
});

export default connect(mapStateToProps, mapDispatchToProps)(FacetPanel);
