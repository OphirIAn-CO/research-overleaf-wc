"""Writing coach: book RAG, per-paper RAG, ChatGPT correction pass. Stdlib only at runtime.

Layout (see the plan in the repo history / README):
  store.py       SQLite helpers (rollback journal, write lease, rebuild-on-damage), hashing, token estimate
  events.py      append-only, metadata-only event log (feeds the Monitor page)
  taxonomy.py    paper parts + synonyms, shared by the book index and the manuscript index
  config.py      paths and the openai.json / ui.json / per-paper settings
  health.py      checks used by `common.py doctor`
"""
__version__ = "0.1.0"
