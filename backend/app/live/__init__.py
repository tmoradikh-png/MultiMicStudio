"""Live Speaker mode (real-time) — kept entirely separate from the frozen offline
record→upload→process audio engine.

Everything here is gated behind ``settings.live_mode_enabled`` and is a best-effort
real-time path: a phone microphone streams live to one or more listeners (web/PC/
host phone) with no upload/processing step. The offline product is the reliability
guarantee; live mode never blocks or modifies it.
"""
