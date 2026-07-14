"""Filesystem adapters for evaluation evidence."""

from .json_files import JsonCaseEvidenceReader, load_json_if_exists

__all__ = ["JsonCaseEvidenceReader", "load_json_if_exists"]
