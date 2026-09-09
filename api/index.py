"""Vercel entry point.

Vercel runs each request in a short-lived serverless function, so it imports the
WSGI application rather than running ``app.py`` as a script.  Everything else —
the solver, the rule base, the explainer — is unchanged; see ``app.py``.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app  # noqa: E402,F401   (Vercel looks for a module-level `app`)
