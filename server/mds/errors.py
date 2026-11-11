class DomainError(Exception):
    status = 400
    code = "error"

    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code


class ValidationError(DomainError):
    status = 422
    code = "validation"


class NotFound(DomainError):
    status = 404
    code = "not_found"


class Conflict(DomainError):
    status = 409
    code = "conflict"


class Forbidden(DomainError):
    status = 403
    code = "forbidden"


class AuthError(DomainError):
    status = 401
    code = "auth"


class VersionConflict(Conflict):
    code = "version_conflict"

    def __init__(self, current_version: int | None = None, current: dict | None = None,
                 message: str = "Дані змінилися, оновіть сторінку"):
        super().__init__(message)
        self.current_version = current_version
        self.current = current
