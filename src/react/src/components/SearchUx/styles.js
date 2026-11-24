import COLOURS from '../../util/COLOURS';

export const searchBoxStyles = theme =>
    Object.freeze({
        root: { position: 'relative', marginBottom: '12px' },
        input: {
            fontFamily: theme.typography.fontFamily,
            background: '#fff',
        },
        popper: { zIndex: 1300 },
        paper: {
            maxHeight: '420px',
            overflowY: 'auto',
            fontFamily: theme.typography.fontFamily,
        },
        groupLabel: {
            padding: '8px 16px 4px',
            fontSize: '12px',
            fontWeight: 700,
            textTransform: 'uppercase',
            color: '#666',
        },
        option: {
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            padding: '6px 16px',
            cursor: 'pointer',
            fontSize: '14px',
            '&[aria-selected="true"]': { background: '#eef3fb' },
        },
        optionMain: { overflow: 'hidden', textOverflow: 'ellipsis' },
        optionKind: { color: '#666', fontSize: '12px', marginRight: '6px' },
        count: {
            color: '#666',
            fontSize: '12px',
            marginLeft: '12px',
            whiteSpace: 'nowrap',
        },
        footer: {
            borderTop: '1px solid #eee',
            padding: '8px 16px',
            fontSize: '13px',
            cursor: 'pointer',
            '&[aria-selected="true"]': { background: '#eef3fb' },
        },
        hint: { padding: '8px 16px', fontSize: '13px', color: '#666' },
    });

export const facetStyles = theme =>
    Object.freeze({
        group: { borderBottom: '1px solid #e6e6e6', padding: '6px 0' },
        header: {
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            cursor: 'pointer',
            fontFamily: theme.typography.fontFamily,
            fontWeight: 600,
            fontSize: '14px',
            padding: '6px 0',
            background: 'none',
            border: 'none',
            width: '100%',
            textAlign: 'left',
        },
        selectedCount: {
            fontSize: '12px',
            fontWeight: 400,
            color: COLOURS.NAVIGATION || '#0d1128',
            marginLeft: '6px',
        },
        within: { margin: '0 0 6px', width: '100%' },
        row: {
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            fontFamily: theme.typography.fontFamily,
            fontSize: '13px',
            minHeight: '28px',
        },
        rowLabel: { display: 'flex', alignItems: 'center', minWidth: 0 },
        rowText: {
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
        },
        zero: { color: '#aaa' },
        count: { color: '#666', fontSize: '12px', marginLeft: '8px' },
        checkbox: { padding: '2px 6px 2px 0' },
        more: {
            fontSize: '12px',
            padding: '4px 0',
            background: 'none',
            border: 'none',
            color: '#1a5bb5',
            cursor: 'pointer',
        },
        unavailable: {
            fontSize: '12px',
            color: '#888',
            margin: '4px 0 8px',
        },
    });

export const feedbackStyles = theme =>
    Object.freeze({
        bar: {
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            fontFamily: theme.typography.fontFamily,
            margin: '4px 0 8px',
            fontSize: '14px',
        },
        total: { fontWeight: 600 },
        chips: { display: 'flex', flexWrap: 'wrap', margin: '0 0 8px' },
        chip: { margin: '0 6px 6px 0', maxWidth: '100%' },
        clearAll: {
            fontSize: '12px',
            background: 'none',
            border: 'none',
            color: '#1a5bb5',
            cursor: 'pointer',
            padding: '0 0 6px',
        },
        notice: {
            fontFamily: theme.typography.fontFamily,
            fontSize: '14px',
            margin: '8px 0',
        },
        link: {
            background: 'none',
            border: 'none',
            padding: 0,
            color: '#1a5bb5',
            cursor: 'pointer',
            fontSize: '14px',
            textAlign: 'left',
        },
        relaxList: { margin: '6px 0 0', paddingLeft: '18px' },
    });
