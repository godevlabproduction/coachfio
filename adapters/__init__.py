"""Game plugins. Each game is an adapter that turns raw input into the core's
Match / Event / Metric / Insight. The core never imports a concrete adapter;
it goes through /adapters/base."""
