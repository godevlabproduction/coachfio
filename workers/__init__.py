"""Async job processing. Uploads must never block a request - the API enqueues,
the worker runs the pipeline."""
