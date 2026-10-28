# Reproduce the checked setup lifecycle

On 2026-10-29 the maintainer ran `python3 -m unittest discover -s tests -p 'test_*.py' -v` on macOS with Python 3.14.5: 31 tests passed. Thirty tests came from the extracted baseline; one verifies that the public default fetcher refuses without network access.

`python3 demo.py` also passed. The temporary example checked read-only planning, revision application, update, restoration and user-edit conflict protection. It reported zero network requests, zero provider calls and no change to the real home directory. Its one fetch operation used an injected local sample.

Tests simulate package and authentication command runners. They do not establish fresh Mac installation, real login, all operating systems, independent adoption, security certification, savings, classroom use or grant selection. Browser examples are separate explanations, not execution of this engine.
