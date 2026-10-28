# Noleji Setup Core

Review managed AI-tool instruction changes before applying them, retain user edits and restore a previous managed revision. This small Python core is extracted from Noleji Start and the managed-skill part of Operator. It is an initial public snapshot, not the full hosted product or a general business-task executor.

[Project introduction](https://noleji-ai.github.io/noleji-setup-core/) · [한국어 안내](README.ko.md) · [MIT license](LICENSE)

## Try the isolated lifecycle

Python 3.11 or later and its standard library are sufficient. From this directory:

```sh
python3 demo.py
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

The example uses a temporary home and local sample releases. It checks planning, applying a revision, updating, restoring and refusing to overwrite user edits. It removes the temporary directory on exit. It makes no network or model calls and does not change your real home directory.

## Understand the operating boundary

This public core has no default remote release feed. Integrators must supply a reviewed fetcher to obtain releases. Direct use of setup and connection methods can change the home path you provide and run tools; inspect source and account permissions first. Tests inject command runners and do not prove fresh Mac installation or real provider authentication.

Browser examples on the introduction page explain state transitions using sample data. They do not run this Python engine or install anything. The downloadable Python example exercises the engine locally.

## Contribute reproducible changes

Report a small reproduction, Python version, expected and actual result through [GitHub issues](https://github.com/noleji-ai/noleji-setup-core/issues). Include no credentials, private instructions or customer/student data. For sensitive findings, follow [SECURITY.md](SECURITY.md).

See [provenance and exclusions](PROVENANCE.md), [maintenance](MAINTENANCE.md) and [verification](VERIFICATION.md). Independent adoption, classroom deployments, savings and OpenAI program selection are unverified. The project is independent and implies no OpenAI endorsement.
