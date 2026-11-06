from ninja import Schema


class ErrorOut(Schema):
    detail: str


class FieldErrorOut(Schema):
    loc: list[str]
    msg: str
    type: str


class FieldErrorsOut(Schema):
    detail: list[FieldErrorOut]


class Page[Item](Schema):
    items: list[Item]
    count: int
