"""Text-level correction walk — correct_text.

Re-exports ``correct_text`` from ``engine.py`` for structural parity with the
JS ``lib/location-correction/index.js`` split.

Consumers may import from here or from ``engine.py`` — both resolve.

Requirements: 3.1, 3.2, 3.9, 3.10, 5.1
"""

from __future__ import annotations

from app.correction.engine import (  # noqa: F401
    TextCorrection,
    TextCorrectionResult,
    correct_text,
)
