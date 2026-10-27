class SourceError(Exception):
    status_code = 502

    def __init__(self, message: str, retry_after: int | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class UserNotFoundError(SourceError):
    status_code = 404


class PrivateAccountError(SourceError):
    status_code = 403


class RateLimitedError(SourceError):
    status_code = 429


class UpstreamError(SourceError):
    status_code = 502
