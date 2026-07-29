"""Word-level correction walk — correct_words.

Re-exports ``correct_words`` from ``engine.py`` for structural parity with the
JS ``lib/location-correction/word-walk.js`` split.

Consumers may import from here or from ``engine.py`` — both resolve.

Requirements: 3.1, 3.2, 5.3, 5.4, 5.5
"""

from __future__ import annotations

from app.correction.engine import (  # noqa: F401
    WordCorrectionResult,
    correct_words,
)
