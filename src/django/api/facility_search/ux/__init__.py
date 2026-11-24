"""
The /map search UX on OpenSearch: scored text search, disjunctive facet
counts, typeahead and "did you mean".

Relevance lives in the cluster, not in Python: the query shapes are
stored mustache search templates and the request/response shaping is done
by named search pipelines (see assets.py). Django only supplies the
filters, the text and which template and pipeline to run, so relevance can
be tuned by installing a new template version without a deploy.
"""
