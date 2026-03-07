from rest_framework.pagination import PageNumberPagination

class DefaultPagination(PageNumberPagination):
    page_size = 10          # default items per page
    page_query_param = "page"
    page_size_query_param = "page_size"
    max_page_size = 50
