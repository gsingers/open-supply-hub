import React from 'react';
import { arrayOf, func, number, shape, string } from 'prop-types';
import { connect } from 'react-redux';
import { withStyles } from '@material-ui/core/styles';

import Highlighted from './Highlighted';
import { commitSearchText, removeFilterGroup } from '../../actions/searchUx';
import { GROUP_LABELS, stripHighlight } from '../../util/searchUx';
import { feedbackStyles } from './styles';

// Below this many results a "did you mean" is worth offering.
const FEW_RESULTS = 3;
const MAX_RELAXATIONS = 3;

// "Did you mean" and, on zero results, the filters whose removal would
// bring results back (with how many). Both come with the facet counts.
function SearchFeedback({
    total,
    didYouMean,
    relaxations,
    query,
    acceptSuggestion,
    relax,
    classes,
}) {
    if (total === null) return null;
    const showDidYouMean =
        didYouMean &&
        total < FEW_RESULTS &&
        stripHighlight(didYouMean.highlighted).toLowerCase() !==
            (query || '').toLowerCase();
    const options = relaxations
        .filter(r => r.count > 0)
        .slice(0, MAX_RELAXATIONS);

    return (
        <div role="status" aria-live="polite">
            {showDidYouMean && (
                <p className={classes.notice}>
                    Did you mean{' '}
                    <button
                        type="button"
                        className={classes.link}
                        onClick={() =>
                            acceptSuggestion(
                                stripHighlight(didYouMean.highlighted),
                            )
                        }
                    >
                        <Highlighted
                            text={didYouMean.highlighted}
                            fallback={didYouMean.text}
                        />
                    </button>
                    ?
                </p>
            )}
            {total === 0 && options.length > 0 && (
                <div className={classes.notice}>
                    No locations match all of these filters. Try:
                    <ul className={classes.relaxList}>
                        {options.map(option => (
                            <li key={option.group}>
                                <button
                                    type="button"
                                    className={classes.link}
                                    onClick={() => relax(option.group)}
                                >
                                    Remove {GROUP_LABELS[option.group] ||
                                        option.group}
                                </button>{' '}
                                → {option.count.toLocaleString()} results
                            </li>
                        ))}
                    </ul>
                </div>
            )}
        </div>
    );
}

SearchFeedback.defaultProps = {
    total: null,
    didYouMean: null,
    query: '',
};

SearchFeedback.propTypes = {
    total: number,
    didYouMean: shape({ text: string, highlighted: string }),
    relaxations: arrayOf(shape({ group: string, count: number })).isRequired,
    query: string,
    acceptSuggestion: func.isRequired,
    relax: func.isRequired,
    classes: shape({}).isRequired,
};

const mapStateToProps = ({
    searchUx: {
        facets: { data },
    },
    filters: { facilityFreeTextQuery },
}) => ({
    total: data ? data.total : null,
    didYouMean: data ? data.did_you_mean : null,
    relaxations: (data && data.relaxations) || [],
    query: facilityFreeTextQuery,
});

const mapDispatchToProps = dispatch => ({
    acceptSuggestion: text => dispatch(commitSearchText(text)),
    relax: group => dispatch(removeFilterGroup(group)),
});

export default connect(
    mapStateToProps,
    mapDispatchToProps,
)(withStyles(feedbackStyles)(SearchFeedback));
