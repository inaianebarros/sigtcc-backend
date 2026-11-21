from django.core.files.storage import InMemoryStorage


class FailingStorage(InMemoryStorage):
    """A bucket that is down: every save fails."""

    def _save(self, name, content):
        raise OSError("bucket unavailable")
