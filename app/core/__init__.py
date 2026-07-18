"""Shared core: state store, Telegram clients, download engine and proxy control.

Everything in this package is UI-agnostic. The FastAPI layer (`app.api`) and the
Telegram bot (`app.bot`) are both thin front-ends over these services.
"""
