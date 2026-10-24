import ReleaseVersion

## The utility interface version is independent of the consumer's release.
## A compiler pin describes the build recipe, not compiler provenance.
ToolVersion := [].{
	version = "0.5.0"
	json = |tool| Json.to_str({ tool, version, contract_version: 1.U64, build_compiler_pin: "nightly-2026-10-09-258ab27" })
}

expect ReleaseVersion.valid(ToolVersion.version)
