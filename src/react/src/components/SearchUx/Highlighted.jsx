import React from 'react';
import { string } from 'prop-types';

import { splitHighlight } from '../../util/searchUx';

// Server-highlighted text as plain text nodes with <mark> around the
// matched parts. Never uses innerHTML: the text is facility data.
export default function Highlighted({ text, fallback }) {
    const pieces = splitHighlight(text);
    if (!pieces.length) {
        return <>{fallback}</>;
    }
    return (
        <>
            {pieces.map((piece, index) =>
                piece.hit ? (
                    // eslint-disable-next-line react/no-array-index-key
                    <mark key={index} className="search-ux-hit">
                        {piece.text}
                    </mark>
                ) : (
                    // eslint-disable-next-line react/no-array-index-key
                    <React.Fragment key={index}>{piece.text}</React.Fragment>
                ),
            )}
        </>
    );
}

Highlighted.defaultProps = {
    text: '',
    fallback: '',
};

Highlighted.propTypes = {
    text: string,
    fallback: string,
};
