"""Correction history persistence.

Public API:
- HistoryRecord: dataclass representing one applied correction
- CorrectionHistoryWriter: bounded queue + background writer
"""

from app.history.writer import CorrectionHistoryWriter, HistoryRecord

__all__ = ["CorrectionHistoryWriter", "HistoryRecord"]
