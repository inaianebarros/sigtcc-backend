from ninja import Schema


class ErrorOut(Schema):
    detail: str


class Page[Item](Schema):
    items: list[Item]
    count: int
