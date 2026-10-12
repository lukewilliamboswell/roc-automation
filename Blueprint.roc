# Development environment and tasks for roc-automation, realised by roc-blueprint with Nix.
#
#     blueprint run check                           # what CI checks of the Roc scripts
#     blueprint run check-settings -- owner/name    # compare a repository's settings with the manual
#     blueprint run nightly-report                  # what actions/nightly-report runs
#     blueprint shell                               # the environment, to run scripts/*.roc directly
#     blueprint update                              # refresh Blueprint.lock
#
# `roc-stable` is the release named below; it runs the scripts under scripts/.
# Nothing here provides a bare `roc`.
app [config] { pf: platform "https://github.com/lukewilliamboswell/roc-blueprint/releases/download/0.4.0/4jmnCGtQeuXTJJYc1rPrnA1pQFPRqUecY5oau1te8cYn.tar.zst" }

config = [
	Name("roc-automation"),
	Overlay("roc", "github:roc-lang/roc-overlay"),
	Environment(
		"dev",
		[
			Overlays(["roc"]),
			Command("roc-stable", "rocpkgs.nightly-2026-10-09-258ab27"),
			# Every bundle the scripts depend on, including dependencies of
			# dependencies: `roc-stable deps scripts/check_all.roc` lists them.
			RocPackages([
				"https://github.com/roc-lang/basic-cli/releases/download/0.25.0/CZsY7tYZwR3rj9kYbpaCfxki2yVAaRL8bBwMLvB2xkbA.tar.zst",
				"https://github.com/roc-lang/http/releases/download/1.0.0/6ZUwqYhCS8PU9Mo6MF7oV82ET2o7KYb57CLKDq4cq4sS.tar.zst",
			]),
			# Programs the scripts start.
			Tools(["coreutils", "gh"]),
		],
	),
	Shell("default", [Use("dev")]),
	# Everything CI checks of the Roc scripts.
	Task("check", [Use("dev"), Run(["scripts/check_all.roc"])]),
	# Compare a repository's GitHub settings with the "Repository settings"
	# chapter. Read-only; needs a `gh` login with administration access.
	Task("check-settings", [Use("dev"), Run(["scripts/check_settings.roc", "--"])]),
	# The shared action that is a Roc script. The action runs its task from
	# its own checkout of this repository, with its inputs in the
	# environment, and Blueprint.lock fixes everything the task starts.
	Task("nightly-report", [Use("dev"), Run(["scripts/nightly_report.roc"])]),
]
