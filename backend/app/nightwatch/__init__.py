"""Nightwatch: headless shadow-mode SRE observer.

The package is intentionally separate from the interactive agent loop. It has
its own worker, store, and API so it can run in its own partition without
sharing chat sessions or request lifecycles.
"""
