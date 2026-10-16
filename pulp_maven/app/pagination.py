from rest_framework.pagination import CursorPagination


class MavenFlatPackageCursorPagination(CursorPagination):
    """Opt-in cursor pagination for the flat Maven package catalog experiment."""

    page_size = 100
    page_size_query_param = "page_size"
    max_page_size = 100
    # Primary key is immutable and unique, so previous/next cannot wrap or repeat.
    # This intentionally differs from the default catalog order for the spike.
    ordering = ("pk",)

    def get_ordering(self, request, queryset, view):
        # Cursor mode rejects the ordering query parameter because DRF encodes only
        # the current position. A client-changing ordering could make that position
        # point into a different sequence and repeat or skip rows. Future support
        # would need to encode an allowlisted ordering identifier in cursor state.
        return self.ordering


class MavenRepositoryVersionCursorPagination(CursorPagination):
    """Opt-in cursor pagination for the Maven repository-version pilot."""

    page_size = 100
    page_size_query_param = "page_size"
    max_page_size = 100
    ordering = "-number"


class MavenPackageCursorPagination(CursorPagination):
    """Opt-in cursor pagination for the distinct Maven package catalog experiment."""

    page_size = 100
    page_size_query_param = "page_size"
    max_page_size = 100
    ordering = ("_cursor_pk",)

    def get_ordering(self, request, queryset, view):
        # Cursor mode rejects the ordering query parameter because DRF encodes only
        # the current position. A client-changing ordering could make that position
        # point into a different sequence and repeat or skip rows. Future support
        # would need to encode an allowlisted ordering identifier in cursor state.
        return self.ordering
