# Roll out the target workflows

Use this page to migrate one repository to the
[target workflows](ideal-workflows.md), and to see how far the rollout has
reached. Update it in the same pull request series as each migration.

## Order of work

The model is proven on three package repositories before any other repository
changes. Each pilot ends by recording its findings below and correcting the
target page.

| Stage | Scope | State |
| --- | --- | --- |
| 0 | Document the target in this repository | In progress |
| 1 | Foundations: Blueprint upgrades, `roc-nightly` alias in setup-roc, `actions/setup-blueprint` | Not started |
| 2 | Pilot: roc-pandoc | Not started |
| 3 | Pilot: roc-ansi; extract shared reusable workflows | Not started |
| 4 | Pilot: roc-parser | Not started |
| 5 | Review the pilots; port this repository's remaining Python to Roc | Not started |
| 6 | Remaining repositories | Not started |

## Foundations required before a pilot

A repository cannot migrate until these exist. Record the reviewed revision of
each when it lands.

| Requirement | Repository | Revision |
| --- | --- | --- |
| A release whose CLI and platform bundle agree | roc-blueprint | 0.4.0 |
| The `Command` environment setting, with a test that runs a `roc-stable` shebang script | roc-blueprint | 0.4.0 |
| Updating a single named input | roc-blueprint | 0.4.0 |
| macOS host execution, exercised in CI | roc-blueprint | 0.4.0; weaver runs it on `macos-latest` |
| `roc-nightly` alias, resolved-tag output, and release-digest verification for the latest nightly | setup-roc | Pending |
| `actions/setup-blueprint` | roc-automation | Pending |

### Caching Nix in CI

Building the `blueprint` CLI is the only expensive step; nixpkgs tools come from
the public cache and `roc-stable` is a prebuilt download. Measure before
choosing, and record cold and warm job times here.

| Option | Cold | Warm | Notes |
| --- | --- | --- | --- |
| Magic Nix Cache | Pending | Pending | Free; confirm whether it needs `id-token: write` |
| Prebuilt `blueprint` release binary, verified by reviewed hash | Pending | Pending | Only an x86_64 Linux binary is published today |
| FlakeHub Cache | Pending | Pending | Paid; unavailable to pull requests from forks; needs `id-token: write` on test jobs |

Prefer the first option that is fast enough. FlakeHub Cache would be an opt-in
input, never a requirement, because fork pull requests cannot use it.

## Migrate a repository

Work from a fresh branch of the remote default branch. Open one pull request per
numbered group so that a failure points at one change.

1. **Environment.** Add `Blueprint.roc` and `Blueprint.lock` with `roc-stable`
   and every tool the scripts call. Confirm `blueprint run` executes a shebang
   script. Remove any `flake.nix`, `.envrc` or other environment definition it
   replaces.
2. **Scripts.** Port each Python or bash script to a Roc script following the
   [script conventions](ideal-workflows.md#write-automation-as-roc-scripts).
   Port each script's tests first, as `expect` tests or cases the check script
   runs. Delete the original only when its replacement is exercised in CI.
3. **Pins.** Remove `.roc-version` and every `roc` header field. Make the check
   script fail if either reappears.
4. **Examples.** Point repository examples at the relative package path. Add the
   examples archive to the release workflow, and test the archive before upload.
   Remove any release follow-up that rewrites committed examples.
5. **Workflows.** Replace the repository's workflows with the
   [four target workflows](ideal-workflows.md#the-four-workflows). Remove
   `update-roc-nightly.yml`, the configuration check caller and
   `.github/roc-nightly.json`.
6. **Repository settings.** Update required check names in the ruleset, delete
   the `automation/roc-nightly` branch once no pull request uses it, and remove
   the repository's row from the nightly updater table in the README.

### Accept a migration

Record a link for each item in the migration pull request.

- `blueprint run check` passes locally with a current nightly `roc` on `PATH`.
- A search finds no `.roc-version`, no `roc: "` header field, and no remaining
  Python or bash automation.
- `ci.yml` passes on the pull request.
- A `release.yml` dry run produces a bundle and an examples archive whose headers
  differ from the repository's only in the package dependency.
- A manual `nightly.yml` run passes against the latest release.
- A deliberately failing nightly run opens exactly one tracking issue, a second
  failure updates it, and a passing run closes it.

## Repository status

Only the three pilots are scheduled. Other repositories keep the nightly updater
until the pilots are reviewed.

| Repository | Model | Notes |
| --- | --- | --- |
| roc-pandoc | Earlier; pilot 1 | Scripts already in Roc with a `roc-stable` shebang |
| roc-ansi | Earlier; pilot 2 | Examples already use the relative package path |
| roc-parser | Earlier; pilot 3 | Has a `Blueprint.roc` for benchmarks; ships an examples archive |
| basic-ssg | Earlier | |
| roc-blueprint | Earlier | Adopts its own `Blueprint.roc` in stage 1 |
| roc-fuzz | Earlier | |
| roc-graph-layout | Earlier | |
| roc-gui | Earlier | Has a `Blueprint.roc`; uses release-policy and publisher actions only |
| roc-pdf | Earlier | |
| roc-platform-template-go | Earlier | Platform: build-input guide applies |
| roc-platform-template-rust | Earlier | Platform: build-input guide applies |
| roc-platform-template-zig | Earlier | Platform: build-input guide applies |
| roc-ray | Earlier | Platform: build-input guide applies |
| roc-signals | Earlier | |
| roc-time | Earlier | |
| roc-wasm4 | Earlier | Platform: build-input guide applies |
| weaver | Target; unscheduled pilot | Migrated in weaver #36 to #40. Carries its own `setup-tooling` action until `actions/setup-blueprint` exists |

## The earlier controller during the rollout

`actions/nightly`, `update-roc-nightly.yml` and `check-config.yml` remain
supported, unchanged, for every repository still listed as Earlier. They receive
security fixes only. They are removed, with their tests, when the last
repository has migrated. Do not port them to Roc.

`actions/check-release`, `actions/publish-build-inputs` and `actions/build-docs`
are kept and ported to Roc in stage 5. Each port carries its regression tests
across first and keeps caller code out of the privileged job.

## Lessons from pilots

Add a dated entry per pilot: what the target page got wrong, what was changed,
and what remains unresolved.

### 2026-10-06: foundations, local chain test

A scratch project on x86_64 Linux installed a `roc-stable` launcher for one
nightly release from the overlay. `blueprint update` then `blueprint run`
executed a `#!/usr/bin/env roc-stable` script on that release, and the caller's
own `roc` stayed visible inside the environment.

- `blueprint run` fetched the `nixpkgs-unstable` channel while entering the
  environment, which `Blueprint.lock` does not record. The cause is not yet
  confirmed. Resolve it before describing the environment as fully pinned.
- Not yet exercised: the same chain in CI, on macOS, and through a published
  roc-blueprint release rather than a source checkout.

### 2026-10-12: weaver, unscheduled pilot

weaver migrated ahead of the three scheduled pilots because its nightly pin
pull requests had failed since 2026-09-23. Every claim below was exercised in
weaver's pull-request CI on `ubuntu-latest`, `macos-latest` and
`windows-latest`, except where it says otherwise.

What the target page got wrong or left open:

- **A platform with a `roc` header pin breaks every consumer that floats.**
  `roc check` exits 2 on a warning, and a dependency whose header pins another
  nightly produces a version-mismatch warning. weaver's examples moved from
  roc-platform-template-zig to basic-cli 0.25.0, which has no pin. A package
  cannot float until every platform its examples use has dropped the field.
- **Windows cannot run Blueprint.** There is no Nix and no `blueprint` binary.
  weaver reads the `roc-stable` tag from `Blueprint.roc`, downloads that
  release from `roc-lang/nightlies`, checks it against the release's recorded
  digest, and installs it as `roc-stable.exe`. Scripts are invoked as
  `roc-stable scripts/<name>.roc`. The target page should say how a Windows job
  gets `roc-stable`, or say that Windows is out of scope.
- **The examples archive needs its expected output.** The target page lists
  only the examples and a README. `nightly.yml` has to know what a release's
  examples should print, and the default branch's spec describes the default
  branch. weaver's archive includes `spec.json`.
- **A release can test the exact archive it uploads.** `roc check`, `test` and
  `build` accept `--replace-dep OLD NEW`. The release workflow builds the
  archive with the real release URL, serves the candidate bundle on loopback,
  and passes the declared URL as `OLD`. Nothing is rebuilt between the test
  and the upload.
- **Roc rejects version `0.0.0` in a package URL.** A dry run needs another
  placeholder; weaver uses `0.0.1-dry-run`.
- **An immutable archive must not be format-checked.** A later formatter
  change would fail `nightly.yml` for a reason no patch release fixes, so the
  archive is checked, tested, built and run, but not passed to `roc fmt`.
- **Pull requests opened with `GITHUB_TOKEN` do not start CI.**
  `update-blueprint.yml` opens its pull request that way, so a maintainer has
  to close and reopen it. A shared workflow needs an answer for this.

Working substitutes for missing foundations:

- `actions/setup-blueprint`: weaver's `.github/actions/setup-tooling` installs
  Nix with `cachix/install-nix-action` and downloads the `blueprint` 0.4.0
  binary, verified against a sha256 written in the action.
- `roc-nightly` alias and resolved-tag output in setup-roc: weaver installs
  the latest nightly as plain `roc` with upstream setup-roc and prints
  `roc version`. `nightly.yml` resolves the tag in its own first job.

Not yet exercised: `nightly.yml`, `update-blueprint.yml` and the publish job of
`release.yml`, which run only from the default branch, and a release that
ships an examples archive.
