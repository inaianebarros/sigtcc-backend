from ninja import Schema


def check_partial_update(schema: Schema, fields: tuple[str, ...]) -> None:
    """A partial update (PATCH) names at least one of the fields and never sets one to null."""
    sent = schema.model_fields_set & set(fields)
    if not sent:
        raise ValueError(f"Informe ao menos um campo: {', '.join(fields)}.")
    for field in sent:
        if getattr(schema, field) is None:
            raise ValueError(f"O campo {field} não pode ser nulo.")


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
