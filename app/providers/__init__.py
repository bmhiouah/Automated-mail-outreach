"""Data providers. Each one knows how to talk to an external source and hand
back normalised records plus the untouched raw payload.

hunter.py is the only one so far. OpenAlex/arXiv (education, papers, background)
would slot in here with the same shape: cached, credit-aware, raw-preserving.
"""