import React, { useState } from 'react';
import { arrayOf, bool, func, number, oneOfType, shape, string } from 'prop-types';
import Checkbox from '@material-ui/core/Checkbox';
import TextField from '@material-ui/core/TextField';
import ExpandMoreIcon from '@material-ui/icons/ExpandMore';
import ExpandLessIcon from '@material-ui/icons/ExpandLess';
import { withStyles } from '@material-ui/core/styles';

import { facetValueLabel, isSelected } from '../../util/searchUx';
import { facetStyles } from './styles';

const valueType = oneOfType([string, number]);

// Selected values first (always shown, with their count), then the most
// frequent others. Zero counts stay visible but greyed, so a selection
// that no longer matches can still be undone.
export const facetRows = (data, selected, labelOf) => {
    const counts = {};
    [...(data.values || []), ...(data.selected || [])].forEach(v => {
        counts[String(v.value)] = v.count;
    });
    const chosen = (selected || []).map(option => ({
        value: option.value,
        label: labelOf(option.value, option.label),
        count: counts[String(option.value)] || 0,
        checked: true,
    }));
    const others = (data.values || [])
        .filter(v => !isSelected(selected, v.value))
        .map(v => ({
            value: v.value,
            label: labelOf(v.value),
            count: v.count,
            checked: false,
        }));
    return [...chosen, ...others];
};

function FacetGroup({
    facet,
    data,
    expandedData,
    selected,
    labelMaps,
    open,
    expanded,
    onToggleValue,
    onExpand,
    classes,
}) {
    const [isOpen, setIsOpen] = useState(open || !!(selected || []).length);
    const [within, setWithin] = useState('');

    const labelOf = (value, fallback) => {
        const label = facetValueLabel(facet.key, value, labelMaps);
        return label === String(value) && fallback ? fallback : label;
    };
    const source = expanded && expandedData ? expandedData : data;
    const needle = within.trim().toLowerCase();
    const rows = facetRows(source, selected, labelOf).filter(
        row => !needle || row.label.toLowerCase().includes(needle),
    );
    const visible = expanded ? rows : rows.slice(0, 6 + (selected || []).length);
    const hasMore = !expanded && (rows.length > visible.length || source.other > 0);
    const chosenCount = (selected || []).length;

    return (
        <div className={classes.group}>
            <button
                type="button"
                className={classes.header}
                aria-expanded={isOpen}
                onClick={() => setIsOpen(!isOpen)}
            >
                <span>
                    {facet.label}
                    {chosenCount > 0 && (
                        <span className={classes.selectedCount}>
                            {chosenCount} selected
                        </span>
                    )}
                </span>
                {isOpen ? <ExpandLessIcon /> : <ExpandMoreIcon />}
            </button>
            {isOpen && (
                <div role="group" aria-label={facet.label}>
                    {expanded && (
                        <TextField
                            className={classes.within}
                            placeholder={`Filter ${facet.label.toLowerCase()} values`}
                            value={within}
                            onChange={e => setWithin(e.target.value)}
                            margin="dense"
                            inputProps={{ 'aria-label': `Filter ${facet.label}` }}
                        />
                    )}
                    {visible.map(row => (
                        <label
                            key={String(row.value)}
                            className={classes.row}
                            htmlFor={`facet-${facet.key}-${row.value}`}
                        >
                            <span
                                className={`${classes.rowLabel} ${
                                    row.count === 0 ? classes.zero : ''
                                }`}
                            >
                                <Checkbox
                                    id={`facet-${facet.key}-${row.value}`}
                                    className={classes.checkbox}
                                    checked={row.checked}
                                    onChange={() =>
                                        onToggleValue(row.value, row.label)
                                    }
                                    color="primary"
                                />
                                <span className={classes.rowText} title={row.label}>
                                    {row.label}
                                </span>
                            </span>
                            <span className={classes.count}>
                                {row.count.toLocaleString()}
                            </span>
                        </label>
                    ))}
                    {!visible.length && (
                        <div className={classes.unavailable}>
                            No values in these results.
                        </div>
                    )}
                    {(hasMore || expanded) && (
                        <button
                            type="button"
                            className={classes.more}
                            onClick={() => onExpand(!expanded)}
                        >
                            {expanded ? 'Show fewer' : 'Show more'}
                        </button>
                    )}
                </div>
            )}
        </div>
    );
}

FacetGroup.defaultProps = {
    expandedData: null,
    selected: [],
    open: false,
    expanded: false,
};

FacetGroup.propTypes = {
    facet: shape({ key: string.isRequired, label: string.isRequired })
        .isRequired,
    data: shape({
        values: arrayOf(shape({ value: valueType, count: number })),
        other: number,
    }).isRequired,
    expandedData: shape({}),
    selected: arrayOf(shape({ value: valueType, label: valueType })),
    labelMaps: shape({}).isRequired,
    open: bool,
    expanded: bool,
    onToggleValue: func.isRequired,
    onExpand: func.isRequired,
    classes: shape({}).isRequired,
};

export default withStyles(facetStyles)(FacetGroup);
