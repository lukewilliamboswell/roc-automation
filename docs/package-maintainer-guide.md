# Maintain a Roc package from development to release

Use this walkthrough to keep development moving with Roc nightlies while giving
package users examples that work with a documented compiler and published release.
The shared automation handles compiler-update PRs and release-policy checks. Your
repository supplies its examples, tests, package bundles and documentation.

## 1. Give each branch and version a clear job

Keep `main` for development. Its package or platform roots declare the exact
nightly used to test current source. Keep public examples useful to someone trying
the library: their application headers declare their own supported compiler and
immutable package and platform URLs. Development tests work on temporary copies
of those examples against current source.

When an upstream stable compiler line becomes available, use a branch such as
`roc-0.1.x` for source compatible with that compiler line. This is a compiler
compatibility branch, not a package version or an LTS commitment.

| Item | What it identifies |
| --- | --- |
| `main` | Current development source and its compiler |
| `roc-0.1.x` | Source maintained for Roc's `0.1.x` compiler line |
| A package release, such as `2.3.1` | One immutable published package version |
| An example's header | The compiler and released dependencies needed to run that application |

The version numbers above illustrate the policy; they do not claim that those
upstream compiler releases exist. To rehearse before a stable compiler is
available, explicitly map one real, exact nightly to a simulated support line and
use another nightly for development. Label the simulation in release metadata,
and remove it when adopting an actual stable compiler. See [compiler policy and
simulation settings](maintenance-releases.md).

## 2. Make the first example easy to run

Give each application a folder containing `main.roc` and any companion modules.
Show a realistic input and useful output. A package user should install the
compiler named by that example, then run the application directly. For an
application stored at `examples/hello/main.roc`:

```sh
roc version
roc examples/hello/main.roc
```

No Python wrapper is required to run a Roc example. Python scripts in a repository
may prepare test copies or release assets; keep those contributor tasks separate
from the user's first run. The header declares a compiler requirement; it does
not install or select a compiler executable for the user.

Put `roc` compiler pins in app/package/platform headers. Pin each application
dependency to its published URL, including its platform. A library compiler pin,
an application compiler pin and a platform dependency are distinct requirements;
there is no central version registry that makes them all the same. Preserve those
requirements in downloadable starters, along with companion files and setup
instructions. The README should link to the latest suitable release and explain
which compiler it supports, rather than imply that the newest package always
supports every compiler line.

## 3. Test the three things users rely on

Use separate checks so a passing source build cannot hide a broken download:

| Check | Compiler | Input |
| --- | --- | --- |
| Public examples | The example's declared compiler | Committed release URLs and complete application files |
| Development | The development root's compiler | Current source, with temporary example copies rebound to it |
| Release candidate | The intended release compiler | The exact archives and starters proposed for publication |

Check, test, run and build where applicable. Verify published downloads from a
fresh cache. Do not rewrite the committed URLs or compiler pins during a public
example check. Updating an example to a new package release is a separate reviewed
change.

If you also promise that an older package works with the newest nightly, add that
as an explicit cross-version check. Passing an example with its older compiler
proves only that documented combination. Existing consumers that require
cross-version compatibility should retain that requirement until they deliberately
review a policy change. See the [validation contract](consumer-validation.md).

## 4. Connect nightly updates without handing tests release authority

Follow the [integration guide](integration.md) to add the scheduled caller and
configuration check, pinned to a reviewed full SHA. Configure the development
roots only. A repository containing a package and a platform might select:

```json
{
  "workflows": ["tests.yaml", "release.yml"],
  "compiler_roots": ["package/main.roc", "platform/main.roc"],
  "auto_merge": false
}
```

The selected headers must agree. Public example headers stay outside this list,
even if their compiler currently happens to match development. The list contains
paths, not duplicate versions. Remove the legacy `.roc-version` when adopting
header pins.

The updater runs on default `main`, proposes a signed pin-only commit, and
explicitly dispatches your listed workflows with `nightly_validation: true`.
Those workflows must validate the candidate without publishing, deploying or
creating release follow-ups. Compatibility-branch compiler updates and source
fixes remain reviewed PRs; this controller does not manage them.

Start with manual review and merging. Exercise a successful candidate, a no-op and
a failure before relying on the schedule. Inspect the actual run commit and PR
head, not just a green badge from an earlier commit.

## 5. Finish the repository settings

Workflow files alone do not enforce the intended process. Before rollout:

- Set read-only default workflow permissions. Grant writes only to the specific
  preparation, reporting, release or deployment jobs that need them; keep candidate
  test code out of privileged controller jobs.
- Allow the reviewed shared workflows and their pinned nested actions. Enable
  Actions PR creation; the bot creates PRs but never approves its own changes.
- Protect `main` and each supported compiler branch. Require current-commit checks
  and the review policy you intend to enforce, with no bot bypass. Verify the
  actual rules using a real PR.
- If deploying Pages, select the intended publishing source and allow the release
  branch in the deployment environment's branch policy. Keep Pages deployment
  permissions separate from validation. Preserve previous version URLs when
  changing how documentation is stored.

GitHub-token-created PR workflows can require manual approval to start. An
explicit validation dispatch avoids depending on that trigger; it must still
verify the current candidate SHA and provide checks accepted by your branch rules.
The nightly controller supplies its dispatch flow. A consumer's release follow-up
needs its own equivalent; merely opening the PR does not complete that work.
Verify that the chosen follow-up creator actually signs its commit and validates
that SHA; configuring a bot name alone does neither.
A green dispatch is not proof that required merge checks are satisfied. Follow the
[manual-mode check integration guidance](integration.md#required-checks-on-manually-merged-bot-prs)
and test a real protected merge; workflow-start approval may still be needed.
[GitHub's trigger rules](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow)
and [environment policies](https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments)
explain the platform behavior.

No PAT or GitHub App is required by this controller. Automatic merging defaults
to off; its opt-in requires additional verified rules and cannot manufacture human
review. Consult the [security model](security-model.md) and [OpenSSF rollout
checklist](openssf.md). Adopting these files is not an OpenSSF compliance claim.

## 6. Publish a tested commit, then update the public experience

1. Prepare a reviewed candidate on the appropriate compiler branch. Use a new
   package version independent of the compiler's version. If development remains
   compatible, it can supply the candidate; otherwise adapt it on the support branch.
2. Dispatch release preparation on that branch. Capture its exact commit SHA,
   check the compiler policy, and test the source and actual proposed bundles.
3. Tag that tested SHA and publish those same artifacts. Record named package URLs,
   digests and the real compiler requirement in the notes and starter kit. A
   multi-package repository should clearly label every package URL.
4. Test the published downloads, generate versioned documentation and deploy it.
   Preserve earlier docs and the site's public landing page. Prefer deployment or
   release artifacts for rendered docs; the [release follow-up contract](consumer-validation.md#release-follow-up-contract)
   describes how to migrate existing tracked documentation safely.
5. Open and validate a signed follow-up PR updating public example URLs and release
   links. Updates to public examples on `main` must preserve the development package
   pins. Changes belonging to a support branch target that branch separately.
   Review and merge after its current-commit checks pass.

A merge commit can have a different SHA from its development parent. Test and tag
the final candidate; matching history is not the acceptance test. A later URL/docs
follow-up can also differ from the release tag without changing the published
package. Compare package contents and artifact digests when making that claim.

If publication stops after creating a tag or some assets, inspect exactly what
exists before retrying. Preserve the tag and tested artifacts. Resume only through
a reviewed recovery procedure that checks source identity and existing assets;
do not rebuild from a moving branch, replace old contents or blindly rerun a job
that expects a new release. Fixing the workflow does not invalidate the recorded
identity of already-tested artifacts.

## 7. Carry fixes forward and record what remains manual

Prefer a focused fix on `main`, then open a reviewed backport PR to each affected
compiler branch. `git cherry-pick -x` records the source commit; test the resulting
commit with that branch's compiler. Forward-port urgent support-branch-first fixes
in the other direction. Published code changes require a new package release.

This implementation does not automatically promote releases, update stable
compiler patches, merge backports or promise LTS. Release-policy exceptions are
explicit and off by default. Keep those decisions visible in your README and
contributor guide. Record successful runs, failures and outstanding setup in the
rollout PR or active task plan; do not call the workflow complete while checks,
review, deployment or the actual user download remain unverified.
