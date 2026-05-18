# Releasing `gigiac-skill`

This repo publishes the Gigiac agent skill as immutable, signed-by-SHA
releases. The hosted bash and PowerShell installers at
`gigiac.com/install-claude-skill.sh` / `.ps1` fetch the skill payload from
this repo at a pinned `v*` tag and verify every downloaded file against
`SHA256SUMS` published at the same tag.

That whole chain falls apart if `SHA256SUMS` and the content it covers
ever drift out of sync, so the release ritual below is load-bearing.
Follow it in order, every time.

---

## Release checklist

In this order. Do not split or reorder.

1. **Edit** — modify `gigiac/SKILL.md` and/or `gigiac/scripts/gigiac_client.py` on `main`.
2. **Regenerate `SHA256SUMS`** — run the one-liner below. Output goes to the repo root.
3. **Commit both in a single commit** — the content change AND the regenerated `SHA256SUMS` go into ONE commit. See "Why one commit" below.
4. **Tag the commit** — `git tag vX.Y.Z` on the same commit as step 3.
5. **Push commit + tag** — `git push origin main && git push origin vX.Y.Z`.
6. **Create the GitHub release** — `gh release create vX.Y.Z --generate-notes` (or via the GitHub UI).
7. **Smoke** — run the installer against the new tag end-to-end against a fake `$HOME`:
   ```
   curl -fsSL https://gigiac.com/install-claude-skill.sh \
     | bash -s -- --dry-run --tag vX.Y.Z --api-key gig_smoketest
   ```
   Confirm payload hashes verify clean and the installer reports the pinned tag.

---

## The one-liner

Run from the repo root:

```bash
# macOS (built-in)
shasum -a 256 gigiac/SKILL.md gigiac/scripts/gigiac_client.py > SHA256SUMS

# Linux (GNU coreutils)
sha256sum gigiac/SKILL.md gigiac/scripts/gigiac_client.py > SHA256SUMS
```

Both produce identical output: GNU coreutils form, one file per line,
`<64-hex-sha>  <relative-path>` (two spaces between sha and path).

The installer's parser tolerates BSD form (`SHA256 (file) = <sha>`) as a
hedge against tooling mistakes, but **generation stays locked to GNU
form** so the bytes on disk are predictable across releases. Don't use
`openssl dgst -sha256` (BSD form) for release generation.

---

## Why one commit

If you split `SHA256SUMS` and the content change across two commits and
then tag the second commit:

- Anyone who pulls the tag gets matching content and checksums (fine).
- Anyone who pulls `main@~1` between the two commits gets content
  whose hashes don't match the in-tree `SHA256SUMS` (broken).
- Future you, looking at git blame six months later, sees two commits
  that look reorderable and may try to squash or revert them in a way
  that breaks the invariant silently.

Tags are immutable in this repo (see "Immutability" below), so once a
broken tag ships there's no recall — the only recovery is cutting a new
tag, which means every installer pinned to the broken tag stays broken
forever for users who don't `--tag latest` themselves out of it.

**Rule:** `SHA256SUMS` lives in the same commit as the bytes it covers.
The tag points at that commit. No drift, ever.

---

## Immutability

The `Immutable release tags` ruleset (id `16557115`) blocks `deletion`,
`non_fast_forward`, and `update` on `refs/tags/v*` with `bypass_actors: []`.
That means:

- You cannot delete a published `v*` tag.
- You cannot force-push a published `v*` tag to a different commit.
- The repo owner cannot bypass either, by design — this is the
  supply-chain guarantee the installer's SHA verification relies on.

If a release goes out broken, the fix is to cut the next patch version
(`v0.1.1` → `v0.1.2`), not to retag.

If you find yourself wanting to disable the ruleset, stop and think hard
about whether you're about to torch the entire supply-chain story.

---

## Versioning

`vMAJOR.MINOR.PATCH`, semver-shaped:

- **PATCH** — non-breaking edits to skill copy, helper internals, docs.
- **MINOR** — new helper methods, new env vars, new optional skill features.
- **MAJOR** — breaking changes to helper signatures, env var renames, anything that requires installed users to take action on update.

The installer defaults to a specific pinned tag in its source. Bumping
that default in the installer is itself a coordinated change in the
`djgelner/gigiac` repo — don't expect users on older installer scripts
to auto-track new tags. They have to either re-run the installer or
pass `--tag <new>` explicitly.

---

## Local sanity check before pushing

```bash
# Re-run the one-liner, then diff against tree state.
shasum -a 256 gigiac/SKILL.md gigiac/scripts/gigiac_client.py | diff - SHA256SUMS
```

Exit 0 means tree and `SHA256SUMS` agree. Exit non-zero means the
content drifted from the checksums — fix before commit.
