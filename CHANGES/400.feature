Added Simple Index and JSON Metadata API endpoints for Maven repositories.
The Simple Index (``/simple/``) lists all ``group_id:artifact_id`` projects in a
repository, supporting HTML and JSON content negotiation. The Metadata endpoint
(``/maven/<package>/json/``) returns detailed artifact metadata including releases
and download URLs.
