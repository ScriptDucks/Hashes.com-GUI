"""Smoke test: loads the GUI without blocking. Used for CI multi-OS testing."""
from __future__ import annotations

import sys

# Verify imports work
from inc.algorithms import validalgs
from inc.hashes_client import HashesClient

# Import GUI (requires display on Linux/macOS)
import tkinter as tk
from hashes_gui import HashesGuiApp

# Create app, schedule destroy, run mainloop briefly
app = HashesGuiApp()
app.after(500, app.destroy)
app.mainloop()
print(f"OK: HashesGuiApp loaded and ran on {sys.platform}")
