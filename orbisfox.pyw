"""Double-click to open OrbisFox (no console window). Same as: python -m orbisfox"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from orbisfox.__main__ import main  # noqa: E402

sys.exit(main())
