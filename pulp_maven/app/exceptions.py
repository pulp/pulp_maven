from gettext import gettext as _

from pulpcore.plugin.exceptions import PulpException


class InvalidIndex(PulpException):
    """The file cannot establish either presence or absence of a path."""

    error_code = "MAV0001"

    def __init__(self, message=""):
        super().__init__()
        self.message = message

    def __str__(self):
        return f"[{self.error_code}] " + _("Invalid path index: {message}").format(
            message=self.message
        )
