import React from 'react';
import { arrayOf, func, shape, string } from 'prop-types';
import { connect } from 'react-redux';
import Chip from '@material-ui/core/Chip';
import { withStyles } from '@material-ui/core/styles';

import {
    commitSearchText,
    removeFilterGroup,
    toggleFacetValue,
} from '../../actions/searchUx';
import {
    FACETS,
    facetValueLabel,
    optionsToLabelMap,
} from '../../util/searchUx';
import { feedbackStyles } from './styles';

// One chip per active filter value, each removable, plus "Clear all".
export const activeChips = (filters, embed, labelMaps) => {
    const chips = [];
    if (filters.facilityFreeTextQuery) {
        chips.push({
            id: 'q',
            label: `“${filters.facilityFreeTextQuery}”`,
            remove: commitSearchText(''),
        });
    }
    FACETS.forEach(facet => {
        if (embed && facet.key === 'contributors') return; // pinned
        (filters[facet.filterKey] || []).forEach(option => {
            const known = facetValueLabel(facet.key, option.value, labelMaps);
            const label =
                known === String(option.value) && option.label
                    ? option.label
                    : known;
            chips.push({
                id: `${facet.key}-${option.value}`,
                label: `${facet.label}: ${label}`,
                remove: toggleFacetValue(facet.key, option.value, label),
            });
        });
    });
    const groups = [
        ['boundary', !!filters.boundary, 'Drawn area'],
        ['lists', (filters.lists || []).length > 0, 'List'],
        [
            'isic',
            (filters.isic4 || []).length + (filters.isic5 || []).length > 0,
            'ISIC code',
        ],
        [
            'native_language_name',
            !!filters.nativeLanguageName,
            `Native name: ${filters.nativeLanguageName}`,
        ],
    ];
    groups.forEach(([group, active, label]) => {
        if (active) {
            chips.push({ id: group, label, remove: removeFilterGroup(group) });
        }
    });
    return chips;
};

function ActiveFilterChips({ chips, dispatchThunk, classes }) {
    if (!chips.length) return null;
    return (
        <div className={classes.chips} aria-label="Active filters">
            {chips.map(chip => (
                <Chip
                    key={chip.id}
                    label={chip.label}
                    className={classes.chip}
                    onDelete={() => dispatchThunk(chip.remove)}
                />
            ))}
            {chips.length > 1 && (
                <button
                    type="button"
                    className={classes.clearAll}
                    onClick={() => chips.forEach(c => dispatchThunk(c.remove))}
                >
                    Clear all
                </button>
            )}
        </div>
    );
}

ActiveFilterChips.propTypes = {
    chips: arrayOf(shape({ id: string, label: string })).isRequired,
    dispatchThunk: func.isRequired,
    classes: shape({}).isRequired,
};

const mapStateToProps = ({
    filters,
    filterOptions,
    embeddedMap: { embed },
}) => ({
    chips: activeChips(filters, embed, {
        countries: optionsToLabelMap(filterOptions.countries.data),
        contributors: optionsToLabelMap(filterOptions.contributors.data),
    }),
});

const mapDispatchToProps = dispatch => ({
    dispatchThunk: thunk => dispatch(thunk),
});

export default connect(
    mapStateToProps,
    mapDispatchToProps,
)(withStyles(feedbackStyles)(ActiveFilterChips));
