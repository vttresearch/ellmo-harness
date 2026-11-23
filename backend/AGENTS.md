# Backend guide

Read the root `AGENTS.md` and `docs/setup.md`. Run the root launcher rather than
starting services from unrelated working directories. The standalone Tk client runs
with `python backend/main.py` from the root, using the documented Tk configuration.
Configure local settings with
the example file or the documented `ELLMO_CONFIG` override; do not edit shared
defaults to include private endpoints or credentials.

Keep REST, WebSocket and Unity message schemas compatible. Tool results must remain
correlated with their invocation. Use mocked providers or local synthetic fixtures
for tests; do not send local documents or run paid model calls implicitly.

Run `PYTHONPATH=backend python -B -m unittest discover -s backend/tests -v` from the
root. Document any external service or hardware validation separately.
