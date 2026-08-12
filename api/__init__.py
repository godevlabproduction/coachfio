"""HTTP surface. Thin: it validates input, moves bytes to the object store, and
enqueues work. All analysis lives in /core behind an adapter."""
