"""
Annotator Tool — CLI Helper for AIS Construction
==================================================

RESPONSIBILITY
    Provides a CLI interface for human annotators to construct
    the Actual Impact Set (AIS) ground truth. Displays code nodes
    and document chunks from the indexed repository and records
    annotator decisions.

INPUTS
    CR text, repository index (SQLite + ChromaDB).

OUTPUTS
    JSON file per annotator per CR containing lists of impacted
    element IDs and justifications.

ARCHITECTURAL CONSTRAINTS
    This tool must NOT display ImpacTracer system output to the
    annotator. Showing system results would contaminate the ground
    truth with confirmation bias per Subbab III.7.2.
"""
from __future__ import annotations

# TODO: Implement annotator CLI interface
