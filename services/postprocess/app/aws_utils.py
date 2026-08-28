"""Shared AWS utility functions.

Provides credential-probing and other cross-cutting AWS helpers used by
both the LLM refiner (bedrock.py) and RAG pipeline (clients.py).
"""

from __future__ import annotations

import botocore.session


def probe_credentials() -> bool:
    """Check whether AWS credentials are resolvable.

    Uses ``botocore.session.get_session().get_credentials()`` to determine
    if credentials exist through the default credential chain (environment
    variables, IAM role, config file, etc.).

    Returns:
        True if credentials are found and have an access key, False otherwise.
        Must never throw — catches all exceptions and returns False.
    """
    try:
        session = botocore.session.get_session()
        credentials = session.get_credentials()
        if credentials is None:
            return False
        resolved = credentials.get_frozen_credentials()
        return resolved is not None and resolved.access_key is not None
    except Exception:
        return False
