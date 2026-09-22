"""Loki 2.9.4.2 pipeline component."""
from __future__ import annotations


class ContextStructureContractError(ValueError):
    """Contract validation error with a stable machine-readable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
