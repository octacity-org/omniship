# Homebrew

`omniship.plugins.homebrew` exports `HomebrewFormula`, `HomebrewCask`,
`HomebrewMode`, `HomebrewTap`, and `Homebrew(ctx).formula(...)` / `.cask(...)`.
Blocks delegate to the same imperative implementation.

Use a brew-enabled runner. `requires=[HomebrewTap("owner/tap")]` installs a public
tap through the GitHub setup resolver. For private taps, supply Git authentication
through the provider before setup; the plugin never embeds credentials in URLs.
The formula/cask must already exist, using the URL-and-SHA256 download style.
This is an updater, not a formula generator or a bottle builder.

Each update selects a named release artifact, computes SHA-256 from its bytes,
and supplies the version, public download URL and checksum to Homebrew's own
`bump-formula-pr` or `bump-cask-pr` command. `checksum_artifact="SHA256SUMS.txt"`
additionally verifies the archive against an imported SHA-256 manifest. Publish
the archive first; the public URL must serve those exact bytes.

Modes:

- `HomebrewMode.PULL_REQUEST` (default): brew opens a PR in the existing tap,
  without forking or opening a browser. Set `HOMEBREW_GITHUB_API_TOKEN` using a
  CI secret with permission to write branches and open PRs in the tap repository.
- `HomebrewMode.DIRECT`: requires `branch="main"` (or your actual branch), a
  clean installed tap on that branch, and HEAD equal to the remote branch.
  Brew writes and commits the update, then a normal Git push publishes it.
  Configure the tap's Git push credentials and author identity in CI.
- `HomebrewMode.WRITE_ONLY`: changes files in the installed tap but does not
  commit, push or open a PR. Useful for review or an existing Git publishing step.

`dry_run=True` checks local artifacts and logs the intended update without running
any external commands. It does not verify remote access or audit the formula.
Failure after a real update may leave local changes/commits; there is no automatic
rollback, force push, merge or branch-protection bypass.

For GitHub, put `SecretRef("HOMEBREW_TOKEN")` in the job's
`HOMEBREW_GITHUB_API_TOKEN` environment variable. The source repository's default
`GITHUB_TOKEN` generally does not grant write access to another repository. The
plugin neither creates tokens nor expands their permissions. Brew owns the PR
implementation; there is no second GitHub client in this plugin.

Version strings and URLs are supplied by your workflow; derive them from a
release input, tag or manifest at execution time. URLs must be HTTPS without
credentials, query strings or fragments. One block describes one archive update;
architecture-dependent casks, multi-URL formulae, source-to-Git transitions and
new tap/formula creation are not modeled by this initial API.

See the [imperative release example](../../examples/homebrew-release/workflow.py)
and the upstream [command reference](https://docs.brew.sh/Manpage).
