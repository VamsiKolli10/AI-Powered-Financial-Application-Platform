# Security

This is a portfolio project under active development. Production security and
reliability guarantees have not been established. Use synthetic financial data
and local development credentials when evaluating it.

## Reporting vulnerabilities

Do not include exploitable vulnerability details, credentials, or personal
financial data in a public issue. Use the repository's Security tab to report
a vulnerability privately if private reporting is enabled. Otherwise, ask the
maintainer for a private contact channel without publishing the details.

Include affected versions, reproduction steps using synthetic data, and the
expected impact. No response-time commitment or supported release window is
currently defined.

## Credentials

Copy `.env.example` to `.env` for local setup. Never commit real API keys,
database dumps, private keys, or Terraform state. If a credential is exposed,
revoke or rotate it; deleting it from the latest commit is insufficient.
