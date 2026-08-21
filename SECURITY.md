# Security

## SpiritAgent release-signing key disclosure (2026-08)

### What happened

Up to and including commit `14ecaf0` (the initial import), the private half of
the release-signing keypair lived at `scripts/secrets/update.key` in this
repository. Because the path was committed at first push, anyone with read
access to the repository — including forks, mirrors, the GitHub Archive, and
CI caches — had access to the RSA-4096 private key.

The matching public key shipped to every released client via
`scripts/secrets/update.pub` (bundled by `client/package.json` `extraResources`
and consulted by the runner updater in
[`client/main/runner/updater.ts`](client/main/runner/updater.ts)). Any party
holding the private key could therefore produce `latest-runner.yml` /
`*-update.yml` manifests that **every existing client would accept**, and have
clients install a chosen malicious `*.whl` + `server.py`. This is the full
release-pipeline impersonation threat.

### What was done

| Date | Action |
|------|--------|
| 2026-08-21 | Keypair rotated. New ECDSA P-256 keypair generated off-repo at `~/.spiritagent/update.{key,pub}` (private); the new public trust anchor was committed at `scripts/release-keys/update.pub`. |
| 2026-08-21 | Trust-anchor directory renamed `scripts/secrets/` → `scripts/release-keys/`, with a README documenting the model. |
| 2026-08-21 | `scripts/lib/UpdateManifest.ps1`'s `Resolve-UpdateSigningKey` rewritten to source the private key from `$env:SPIRITAGENT_UPDATE_SIGNING_KEY` or `~/.spiritagent/update.key`. The repo can no longer locate a signing key directly — release builds must have the key injected by whoever runs them. |
| 2026-08-21 | `*.key` (and `*.p12`, `*.pfx`) added to `.gitignore`. |
| 2026-08-21 | `scripts/secrets/update.key` excised from every commit by `git filter-repo --invert-paths --path scripts/secrets/update.key` on the `security/rotate-update-signing-key` branch. All commit SHAs preceding the rewrite were invalidated; `main` was force-pushed to the rewritten history. |

The RSA key in the initial commit **must be considered permanently untrusted**.
Even after the history rewrite, every existing fork, mirror, archive, and
CI cache prior to 2026-08-21 still contains the original blob.

### What users must do

The old public key was bundled into every released client. After 2026-08-21,
only clients that bundle the **new** `update.pub` (at
`scripts/release-keys/update.pub`) can be reached by the new release pipeline.

| Client state | Recommended action |
|--------------|--------------------|
| Installed builds ≤ 2026-08-21 release | **Stop auto-update immediately.** These clients still trust the old `.pub`; an attacker could publish a forged update channel against them until further notice. Use the next installer's full installer binary rather than relying on auto-update. |
| Installed builds ≥ 2026-08-21 release (when released) | Run normally. Auto-update is anchored against the rotated keypair. |

### Upgrading the trust anchor in shipped clients

The next client release must:

1. Bundle `scripts/release-keys/update.pub` via the existing
   `client/package.json` `extraResources` entry.
2. Verify the contents of that file match the **new** ECDSA P-256 public key,
   not the old RSA-4096 public key, before tagging.

If a release is tagged with the old public key, the new manifest will be
rejected by clients running the old build — a useful, intentional failure
mode during the transition window.

### What to do if you must keep auto-update on a ≤ 2026-08-21 build

Until you can install the new client release, you may pin the auto-update
channel to "no source" (the client already tolerates a missing `update.pub`:
runner signature verification will simply fail and abort the update, leaving
the existing install intact). See
[`client/main/runner/updater.ts`](client/main/runner/updater.ts#L402) for the
verifier behaviour — by design it returns `false` rather than installing on
unverified manifests.

### Followups (filed as separate issues)

- Migrate signing to GitHub Actions OIDC + `cosign sign-blob` so the private
  key never leaves the CI trust boundary.
- Add a pre-commit / pre-receive hook that blocks commits containing PEM
  private-key headers.
