# Immutable version 1 of the embedded roc-package profile.
# Documentation applies to package Roc sources; spelling also checks prose.
# Both rules tolerate repositories without matching files.
profile = {
	schema_version: 2,
	rules: [
		{
			id: "public_docs",
			kind: PublicDocs,
			include: ["package/**/*.roc"],
			require_match: False,
			level: Error,
		},
		{
			id: "spelling",
			kind: Spelling,
			include: ["**/*.roc", "**/*.md", "**/*.markdown", "**/*.adoc", "**/*.txt", "**/*.rst"],
			channels: [Comments, Identifiers, Prose],
			require_match: False,
			level: Error,
		},
	],
}
