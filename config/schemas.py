from ninja import Schema


class ErrorOut(Schema):
    detail: str
