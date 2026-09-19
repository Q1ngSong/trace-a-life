"""Domain access for a ``Trace a Life`` case file."""

from .case import CaseDocument, CaseFormatError, ReferenceIndex, load_case

__all__ = ["CaseDocument", "CaseFormatError", "ReferenceIndex", "load_case"]
