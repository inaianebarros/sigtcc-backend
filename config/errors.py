class FieldValidationError(Exception):
    def __init__(self, errors: dict[str, str]):
        super().__init__(errors)
        self.errors = errors


class ConflictError(Exception):
    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail
