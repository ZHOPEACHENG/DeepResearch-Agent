"""
Shared SSE (Server-Sent Events) helpers.

The ``{"event": ..., "data": ...}`` dict shape is the contract between
services and the API layer.  A single definition prevents drift when the
shape evolves.
"""


def sse_event(event: str, data: dict) -> dict:
    """Build an SSE event dict consumed by the API layer."""
    return {"event": event, "data": data}
