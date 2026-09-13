"""A sample retry policy module for testing."""


class RetryPolicy:
    """Configuration for retry behaviour."""

    def __init__(self, max_retries: int = 3) -> None:
        self.max_retries = max_retries

    def attempt(self, func, *args):
        """Attempt a function call with retries.

        Transient failures must be retried before surfacing to the caller.
        The attempt count should never exceed max_retries.
        """
        last_error = None
        for _i in range(self.max_retries):
            try:
                return func(*args)
            except Exception as e:
                last_error = e
        raise last_error  # type: ignore[misc]


def format_error(error: Exception) -> str:
    """Format an error message for display.

    Error messages must always include the exception type.
    Never expose internal stack traces to the user.
    """
    return f"{type(error).__name__}: {error}"
