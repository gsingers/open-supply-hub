import React, { useEffect, useMemo, useRef, useState } from 'react';
import { arrayOf, bool, func, number, shape, string } from 'prop-types';
import { connect } from 'react-redux';
import { withRouter } from 'react-router-dom';
import debounce from 'lodash/debounce';
import TextField from '@material-ui/core/TextField';
import InputAdornment from '@material-ui/core/InputAdornment';
import IconButton from '@material-ui/core/IconButton';
import Paper from '@material-ui/core/Paper';
import Popper from '@material-ui/core/Popper';
import ClickAwayListener from '@material-ui/core/ClickAwayListener';
import CircularProgress from '@material-ui/core/CircularProgress';
import SearchIcon from '@material-ui/icons/Search';
import CloseIcon from '@material-ui/icons/Close';
import { withStyles } from '@material-ui/core/styles';

import {
    applySuggestedFilter,
    clearSuggestions,
    commitSearchText,
    fetchSuggestions,
} from '../../actions/searchUx';
import { makeFacilityDetailLink } from '../../util/util';
import { SUGGEST_DELAY_MS } from '../../util/searchUx';
import { searchBoxStyles } from './styles';

const LISTBOX_ID = 'search-ux-suggestions';

const KIND_LABELS = Object.freeze({
    country: 'Country',
    contributor: 'Contributor',
    parent_company: 'Parent company',
    sector: 'Sector',
    product_type: 'Product type',
    facility_type: 'Location type',
    processing_type: 'Processing type',
    contributor_type: 'Contributor type',
});

const formatCount = count => Number(count).toLocaleString();

// Flat, keyboard-navigable option list built from the grouped response.
const buildOptions = (data, text) => {
    const options = [];
    if (data && data.os_id) {
        options.push({ type: 'os_id', osId: data.os_id });
    }
    ((data && data.facilities) || []).forEach(facility =>
        options.push({ type: 'facility', facility }),
    );
    ((data && data.filters) || []).forEach(filter =>
        options.push({ type: 'filter', filter }),
    );
    if (text.trim()) {
        options.push({ type: 'text', text: text.trim() });
    }
    return options;
};

function OptionContent({ option, classes }) {
    if (option.type === 'os_id') {
        return <span>Go to location {option.osId}</span>;
    }
    if (option.type === 'facility') {
        const { facility } = option;
        return (
            <>
                <span className={classes.optionMain}>{facility.name}</span>
                <span className={classes.count}>
                    {facility.country_code}
                    {' · '}
                    {facility.contributors_count} contrib.
                </span>
            </>
        );
    }
    if (option.type === 'filter') {
        const { filter } = option;
        return (
            <>
                <span className={classes.optionMain}>
                    <span className={classes.optionKind}>
                        {KIND_LABELS[filter.kind]}:
                    </span>
                    {filter.label}
                </span>
                <span className={classes.count}>
                    {formatCount(filter.count)}
                </span>
            </>
        );
    }
    return null;
}

OptionContent.propTypes = {
    option: shape({ type: string.isRequired }).isRequired,
    classes: shape({}).isRequired,
};

function UnifiedSearchBox({
    committedText,
    suggestions,
    fetching,
    placeholder,
    label,
    classes,
    history,
    requestSuggestions,
    dropSuggestions,
    commitText,
    applyFilter,
}) {
    const [text, setText] = useState(committedText);
    const [open, setOpen] = useState(false);
    const [active, setActive] = useState(-1);
    const anchor = useRef(null);

    // A chip removal or reset elsewhere changes the committed text.
    useEffect(() => setText(committedText), [committedText]);

    const debouncedRequest = useMemo(
        () => debounce(value => requestSuggestions(value), SUGGEST_DELAY_MS),
        [requestSuggestions],
    );
    useEffect(() => () => debouncedRequest.cancel(), [debouncedRequest]);

    const options = buildOptions(open ? suggestions : null, text);
    const firstFilterIndex = options.findIndex(o => o.type === 'filter');
    const firstFacilityIndex = options.findIndex(o => o.type === 'facility');

    const close = () => {
        setOpen(false);
        setActive(-1);
        debouncedRequest.cancel();
        dropSuggestions();
    };

    const choose = option => {
        close();
        if (option.type === 'os_id') {
            history.push(makeFacilityDetailLink(option.osId));
        } else if (option.type === 'facility') {
            history.push(makeFacilityDetailLink(option.facility.os_id));
        } else if (option.type === 'filter') {
            setText(committedText);
            applyFilter(option.filter);
        } else {
            commitText(option.text);
        }
    };

    const onChange = event => {
        const { value } = event.target;
        setText(value);
        setOpen(true);
        setActive(-1);
        debouncedRequest(value);
    };

    const onKeyDown = event => {
        if (event.key === 'ArrowDown') {
            event.preventDefault();
            setOpen(true);
            setActive(Math.min(active + 1, options.length - 1));
        } else if (event.key === 'ArrowUp') {
            event.preventDefault();
            setActive(Math.max(active - 1, -1));
        } else if (event.key === 'Enter') {
            event.preventDefault();
            if (open && active >= 0 && options[active]) {
                choose(options[active]);
            } else {
                close();
                commitText(text);
            }
        } else if (event.key === 'Escape') {
            close();
        }
    };

    const clear = () => {
        setText('');
        close();
        commitText('');
    };

    const showPopper = open && text.trim().length > 0;

    return (
        <ClickAwayListener onClickAway={close}>
            <div className={classes.root} ref={anchor}>
                <TextField
                    id="search-ux-input"
                    label={label}
                    placeholder={placeholder}
                    value={text}
                    onChange={onChange}
                    onKeyDown={onKeyDown}
                    onFocus={() => text && setOpen(true)}
                    variant="outlined"
                    margin="dense"
                    fullWidth
                    autoComplete="off"
                    inputProps={{
                        role: 'combobox',
                        'aria-expanded': showPopper,
                        'aria-controls': LISTBOX_ID,
                        'aria-autocomplete': 'list',
                        'aria-activedescendant':
                            active >= 0 ? `search-ux-option-${active}` : '',
                    }}
                    InputProps={{
                        className: classes.input,
                        endAdornment: (
                            <InputAdornment position="end">
                                {fetching && <CircularProgress size={16} />}
                                {text ? (
                                    <IconButton
                                        aria-label="Clear search"
                                        onClick={clear}
                                    >
                                        <CloseIcon />
                                    </IconButton>
                                ) : null}
                                <IconButton
                                    aria-label="Search"
                                    onClick={() => {
                                        close();
                                        commitText(text);
                                    }}
                                >
                                    <SearchIcon />
                                </IconButton>
                            </InputAdornment>
                        ),
                    }}
                />
                <Popper
                    open={showPopper}
                    anchorEl={anchor.current}
                    placement="bottom-start"
                    className={classes.popper}
                    style={{
                        width: anchor.current
                            ? anchor.current.clientWidth
                            : undefined,
                    }}
                >
                    <Paper className={classes.paper}>
                        <div role="listbox" id={LISTBOX_ID}>
                            {options.map((option, index) => (
                                <React.Fragment
                                    key={`${option.type}-${
                                        option.osId ||
                                        (option.facility &&
                                            option.facility.os_id) ||
                                        (option.filter &&
                                            `${option.filter.kind}-${option.filter.value}`) ||
                                        'text'
                                    }`}
                                >
                                    {index === firstFacilityIndex && (
                                        <div className={classes.groupLabel}>
                                            Production locations
                                        </div>
                                    )}
                                    {index === firstFilterIndex && (
                                        <div className={classes.groupLabel}>
                                            Add a filter
                                        </div>
                                    )}
                                    <div
                                        id={`search-ux-option-${index}`}
                                        role="option"
                                        tabIndex={-1}
                                        aria-selected={index === active}
                                        className={
                                            option.type === 'text'
                                                ? classes.footer
                                                : classes.option
                                        }
                                        onMouseDown={event =>
                                            event.preventDefault()
                                        }
                                        onClick={() => choose(option)}
                                        onMouseEnter={() => setActive(index)}
                                    >
                                        {option.type === 'text' ? (
                                            <span>
                                                Search all names for “
                                                {option.text}” ↵
                                            </span>
                                        ) : (
                                            <OptionContent
                                                option={option}
                                                classes={classes}
                                            />
                                        )}
                                    </div>
                                </React.Fragment>
                            ))}
                            {suggestions &&
                                !fetching &&
                                options.length === 1 && (
                                    <div className={classes.hint}>
                                        No suggestions under the current
                                        filters.
                                    </div>
                                )}
                        </div>
                    </Paper>
                </Popper>
            </div>
        </ClickAwayListener>
    );
}

UnifiedSearchBox.defaultProps = {
    suggestions: null,
    placeholder: 'Name, OS ID, country, brand, product…',
    label: 'Search',
};

UnifiedSearchBox.propTypes = {
    committedText: string.isRequired,
    suggestions: shape({
        facilities: arrayOf(shape({ os_id: string, name: string })),
        filters: arrayOf(shape({ kind: string, label: string, count: number })),
    }),
    fetching: bool.isRequired,
    placeholder: string,
    label: string,
    classes: shape({}).isRequired,
    history: shape({ push: func.isRequired }).isRequired,
    requestSuggestions: func.isRequired,
    dropSuggestions: func.isRequired,
    commitText: func.isRequired,
    applyFilter: func.isRequired,
};

const mapStateToProps = ({
    filters: { facilityFreeTextQuery },
    searchUx: { suggest },
    embeddedMap: { embed, config },
}) => ({
    committedText: facilityFreeTextQuery || '',
    suggestions: suggest.data,
    fetching: suggest.fetching,
    label: embed && config.text_search_label ? config.text_search_label : 'Search',
});

const mapDispatchToProps = dispatch => ({
    requestSuggestions: value => dispatch(fetchSuggestions(value)),
    dropSuggestions: () => dispatch(clearSuggestions()),
    commitText: value => dispatch(commitSearchText(value)),
    applyFilter: filter => dispatch(applySuggestedFilter(filter)),
});

export default withRouter(
    connect(
        mapStateToProps,
        mapDispatchToProps,
    )(withStyles(searchBoxStyles)(UnifiedSearchBox)),
);
