"""The Postgres /api/facilities/ search, shared by the view and harness."""
from api.models.facility.facility_index import FacilityIndex
from api.services.facility_processing_filter import FacilityProcessingFilter
from api.services.facility_processing_query import FacilityProcessingQuery


def legacy_search_queryset(params, sort_by):
    """FacilityIndex filtered by the request params, annotated and ordered."""
    facility_processing_filter = FacilityProcessingFilter.from_result(
        FacilityProcessingQuery(params).parse()
    )
    queryset = (
        FacilityIndex.objects.filter_by_query_params(
            params,
            facility_processing_filter=facility_processing_filter,
        )
    )
    order_list = []

    # 'relevance' needs OpenSearch; Postgres orders it like the default.
    if sort_by in (None, 'contributors_desc', 'relevance'):
        order_list = ['-contributors_count', 'name']
    elif (sort_by == 'name_asc'):
        order_list = ['name']
    elif (sort_by == 'name_desc'):
        order_list = ['-name']
    elif (sort_by == 'contributors_asc'):
        order_list = ['contributors_count', 'name']

    queryset, has_fp_relevance = (
        facility_processing_filter.annotate_relevance(queryset)
    )
    if has_fp_relevance:
        order_list.insert(0, '-_fp_relevance')
    order_list.append('id')

    return queryset.extra(order_by=order_list)
