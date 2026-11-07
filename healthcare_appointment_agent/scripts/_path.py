"""Lets `python scripts/x.py` import the clinic_agent package from the project root."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
