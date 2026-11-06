from ninja import Field, Schema
from ninja.conf import settings as ninja_settings
from ninja.pagination import LimitOffsetPagination

MAX_OFFSET = 2**31 - 1


class BoundedLimitOffsetPagination(LimitOffsetPagination):
    class Input(Schema):
        limit: int = Field(
            ninja_settings.PAGINATION_PER_PAGE, ge=1, le=ninja_settings.PAGINATION_MAX_LIMIT
        )
        offset: int = Field(0, ge=0, le=MAX_OFFSET)
