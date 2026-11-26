import os
import sys
import tempfile
from pathlib import Path

# The core module picks its data folder from LOCALAPPDATA when it's imported,
# so point that at a throwaway folder before any test imports it.
os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="tunnelpulse-tests-")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
