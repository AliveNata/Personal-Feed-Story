class SourceError(Exception):
    status_code = 502


class UserNotFoundError(SourceError):
    status_code = 404


class PrivateAccountError(SourceError):
    status_code = 403


class RateLimitedError(SourceError):
    status_code = 429


class UpstreamError(SourceError):
    status_code = 502
