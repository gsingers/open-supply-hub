import {
    HIGHLIGHT_POST,
    HIGHLIGHT_PRE,
    facetValueLabel,
    isSelected,
    searchQueryString,
    splitHighlight,
    stripHighlight,
    toggleOption,
    visibleFacets,
} from '../../util/searchUx';
import { facetRows } from '../../components/SearchUx/FacetGroup';
import { activeChips } from '../../components/SearchUx/ActiveFilterChips';

const hl = text => `${HIGHLIGHT_PRE}${text}${HIGHLIGHT_POST}`;

describe('splitHighlight', () => {
    it('splits marked text into hit and plain pieces', () => {
        expect(splitHighlight(`Blue ${hl('Denim')} Works`)).toEqual([
            { text: 'Blue ', hit: false },
            { text: 'Denim', hit: true },
            { text: ' Works', hit: false },
        ]);
    });

    it('keeps markup-looking facility text as plain text', () => {
        const pieces = splitHighlight(`<img src=x> ${hl('Mill')}`);
        expect(pieces[0]).toEqual({ text: '<img src=x> ', hit: false });
        expect(stripHighlight(`<b>${hl('x')}`)).toBe('<b>x');
    });

    it('handles empty input and an unterminated marker', () => {
        expect(splitHighlight('')).toEqual([]);
        expect(splitHighlight(`${HIGHLIGHT_PRE}abc`)).toEqual([
            { text: 'abc', hit: true },
        ]);
    });
});

describe('facet selection helpers', () => {
    it('toggles values regardless of number/string type', () => {
        const selected = [{ value: 7, label: 'Brand A' }];
        expect(isSelected(selected, '7')).toBe(true);
        expect(toggleOption(selected, '7', 'Brand A')).toEqual([]);
        expect(toggleOption(selected, 8, 'Brand B')).toHaveLength(2);
    });

    it('labels coded values from option lists', () => {
        const maps = { countries: { IN: 'India' } };
        expect(facetValueLabel('countries', 'IN', maps)).toBe('India');
        expect(facetValueLabel('sectors', 'Apparel', maps)).toBe('Apparel');
        expect(facetValueLabel('claim_status', 'claimed', maps)).toBe(
            'Claimed',
        );
    });
});

describe('visibleFacets', () => {
    it('shows every facet outside embed mode', () => {
        expect(visibleFacets(false).map(f => f.key)).toContain(
            'contributors',
        );
    });

    it('in embed mode hides contributor facets and gates extended ones', () => {
        const keys = visibleFacets(true, ['product_type']).map(f => f.key);
        expect(keys).not.toContain('contributors');
        expect(keys).not.toContain('contributor_types');
        expect(keys).toContain('product_type');
        expect(keys).not.toContain('parent_company');
        expect(keys).toContain('countries');
    });
});

describe('facetRows', () => {
    it('lists selected values first, with counts, even when outside top N', () => {
        const data = {
            values: [
                { value: 'IN', count: 9 },
                { value: 'BD', count: 4 },
            ],
            selected: [{ value: 'PE', count: 0 }],
        };
        const rows = facetRows(
            data,
            [{ value: 'PE', label: 'PE' }],
            value => ({ IN: 'India', BD: 'Bangladesh', PE: 'Peru' }[value]),
        );
        expect(rows.map(r => [r.label, r.count, r.checked])).toEqual([
            ['Peru', 0, true],
            ['India', 9, false],
            ['Bangladesh', 4, false],
        ]);
    });
});

describe('activeChips', () => {
    const filters = {
        facilityFreeTextQuery: 'denim',
        countries: [{ value: 'IN', label: 'IN' }],
        contributors: [{ value: 7, label: 'Brand A' }],
        boundary: { type: 'Polygon' },
    };

    it('makes a removable chip per value with readable labels', () => {
        const labels = activeChips(filters, false, {
            countries: { IN: 'India' },
        }).map(c => c.label);
        expect(labels).toEqual([
            '“denim”',
            'Country: India',
            'Data contributor: Brand A',
            'Drawn area',
        ]);
    });

    it('never offers removing the embed contributor', () => {
        const labels = activeChips(filters, true, {}).map(c => c.label);
        expect(labels).not.toContain('Data contributor: Brand A');
    });
});

describe('searchQueryString', () => {
    it('appends facet params to the normal search query string', () => {
        const qs = searchQueryString({ countries: [{ value: 'IN' }] }, false, {
            facet: ['countries', 'sectors'],
            facet_size: '10',
            empty: '',
        });
        expect(qs).toBe(
            'countries=IN&facet=countries&facet=sectors&facet_size=10',
        );
    });
});
