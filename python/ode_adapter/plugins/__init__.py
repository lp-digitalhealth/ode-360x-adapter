"""Built-in plugins. Importing this package registers them via decorators."""
from .fhir import generic_r4, onyx          # noqa: F401
from .ihe import json_envelope, xdm_zip, direct_smtp, http_transport   # noqa: F401
