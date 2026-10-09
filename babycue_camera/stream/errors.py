"""Stream error hierarchy. Messages are written for display to the user."""


class StreamError(Exception):
    """Base class for stream failures."""

    #: Whether retrying the same address later could plausibly succeed.
    retryable = True


class StreamConnectError(StreamError):
    """The host could not be reached (refused, unreachable, DNS failure)."""


class StreamTimeout(StreamError):
    """The host did not respond or stopped sending data within the timeout."""


class StreamHttpError(StreamError):
    """The server answered with an HTTP error status."""

    def __init__(self, message: str, status: int):
        super().__init__(message)
        self.status = status
        self.retryable = status >= 500


class StreamAuthError(StreamHttpError):
    """The server requires (different) credentials."""

    retryable = False

    def __init__(self, message: str, status: int = 401):
        super().__init__(message, status)
        self.retryable = False


class StreamProtocolError(StreamError):
    """The endpoint responded, but not with a usable MJPEG stream."""

    retryable = False


class StreamEnded(StreamError):
    """The server closed the stream."""
