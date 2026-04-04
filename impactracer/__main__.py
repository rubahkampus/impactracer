"""
ImpacTracer Entry Point
=======================
Enables execution via `python -m impactracer`.
Delegates all command routing to cli.py.
"""
from impactracer.cli import app

if __name__ == "__main__":
    app()
