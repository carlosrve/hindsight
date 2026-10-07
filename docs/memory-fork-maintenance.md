# Memory fork maintenance

This is the maintained-fork procedure. The initial upstream base is `b32e992b480064b2bb878fd8ba9e67e4de3fcd74`. Newer upstream commits are not implicitly approved for installation. This fork currently changes client integrations and test/CI support; it does not patch the memory engine itself.

## Branches and releases

- `main` mirrors upstream without our patches. Sync it by verified fast-forward; never force-reset it to repair an apparent divergence from shallow history.
- `integration/memory-20261006` starts from the initial upstream base and receives reviewed feature commits. It is the first assembly target, not a deployed release.
- Feature branches remain independent: Hermes clocks before durable outbox; bank retrieval before Codex clocks. Keep PRs #1–#4 as their review records. Squash each feature into one integration commit, including its tests and generated documentation.
- `test/memory-fixes-20261005` remains the historical experimental composition. It is not an installation source. PR #5 was first reviewed against that composition, then rebased onto the integration after #1/#2 to bootstrap the scoped gate before #3/#4. Its diff contains only maintenance. Pending features inherit this workflow and run it on their own HEADs; integration pushes also verify the exact final assembled commit. No legacy cancelled run is counted as successful.
- Each later upstream update starts a new `integration/<cycle>` from its selected upstream base. Do not rebase or force-push old integration branches, deployed tags or published release commits.
- Release tags use `memory-fork-<YYYYMMDD>.<sequence>`. Create a tag only after the exact composed SHA passes validation and is approved for release. Record the upstream base, active patches and Hermes contract SHA. Never install from a moving branch or `latest` image.

## Patch inventory

`memory-patches.json` records each feature's issue, fork PR, source branch/HEAD, dependencies, regression files and retirement criteria. Source HEADs are evidence for this review cycle, not permanent installation pins. Update the inventory in each maintenance PR; after approved squash merges, record their integration commit SHAs. A null integration commit means the feature has not been merged, not that it is absent from the experimental test branch.

Statuses are `active`, `partially-resolved`, `superseded` and `discarded`. No patch becomes superseded solely because an issue or external PR closes. Our CI changes remain fork-only; engine/client bugs continue to be reported through the existing upstream issues without replacement external PRs.

## Update cycle

1. Inspect local worktrees, dirty files and remote branches. Fetch before comparing histories. Preserve the current installation pins and integration branch.
2. Select an upstream release or an explicitly justified commit. Review API and plugin contracts, package requirements, database migrations and copied CI job commands. Fast-forward mirror main separately from preparing a release.
3. Create a fresh integration branch from that upstream base. Review the active patch inventory and dependencies.
4. Test the upstream behavior without our implementation using each regression oracle. If fixed, omit our code and retain useful regression coverage in a separate test-only commit. If partially fixed, reduce the patch. If unresolved, replay the feature's last reviewed squash commit with `git cherry-pick -x` on a new feature branch and open a PR for that cycle.
5. Resolve conflicts by inspecting changed contracts, not by blindly taking ours/theirs. Rebase only unpublished or in-review feature branches; inspect work before rewriting, preserve a backup ref and use `--force-with-lease` when an intentional remote feature rewrite is needed. Never rewrite published release history.
6. Integrate features in dependency order via reviewed PRs. After a parent squash merge, rebuild/rebase the child onto the new integration parent, retarget its PR and inspect the diff; do not merely retarget and assume it excludes the parent's implementation. Rerun checks on the resulting HEAD.
7. Verify the exact combined revision through the memory fork workflow: Codex unit/type/build, provider tests, real pinned-host callback tests, Hermes main structural compatibility, complete stub-backed API/Postgres system stories, API typecheck, docs build and generated-file validation. All selected jobs must pass. No live-model credential or production data is needed for this gate; it does not establish production semantic-model quality or full upstream provider-matrix success.
8. Document retained/reduced/retired patches, changed contracts and test evidence in the release maintenance PR. Prepare exact service/plugin pins and rollback instructions. Tag/release and deploy require their separate approval; passing CI does not perform either.

## Cadence and retirement

The daily follow-up monitors issues, review requests, feature CI and equivalent upstream fixes. Assess possible base updates weekly; expedite a relevant security or blocking compatibility fix. Do not automatically rebuild or deploy whenever upstream main changes.

When upstream passes a feature's regression without our implementation, remove it from the next integration, record the upstream implementing SHA and mark it superseded. Preserve the test where it remains useful. Check both positive behavior and negative boundaries (scope, synthetic/tool data and replay), plus dependent features. Test Hermes host-contract retirement separately; a Hindsight update does not prove that the installed Hermes host supplies source timestamps.

## Delivery and migration

Service delivery lives in `srv-hindsight`; it must pin an approved artifact digest or source SHA and define the reproducible build. Client patches also require corresponding Hermes and Codex plugin pins; upgrading only the server cannot install these integration fixes. Prepare the service MR after choosing a validated release. Never edit deployed Compose directly. After the user merges and explicitly approves an idle-service deployment, pull the merged checkout, build/pull/recreate, and verify source, artifact, runtime and external endpoint. Any approved /etc change follows etckeeper rules.

Before switching live memory connections, migrate Honcho and the original Hermes/Codex session sources into isolated Hindsight banks. Preserve source clocks, authors, sessions and associations; deduplicate overlapping sources. Validate coverage and retrieval, repeat a clean import with a controlled delta, then approve the connection switch. Keep Honcho as rollback evidence. Installing integrations and importing history are separate operations; the migration must pass before the real switch.

Rollback pins preserve the prior release. Database migrations require their own compatibility/backup assessment; rolling back an image does not imply a database can be downgraded. Keep configuration/secrets outside this public fork and reference private deployment configuration only in its own repository.

Inventory regression paths for not-yet-integrated features may refer to files still on their source branches. After assembly, verify every path against the integration tree and record the squash commit of each feature.
