import "../../tests/fixtures/release-versions.json" as cases_json : Str

## One bounded release identity policy for planning and publication. Build
## metadata is deliberately unsupported; prerelease identifiers follow SemVer.
ReleaseVersion := [].{
	prerelease : Str -> Try(Bool, Str)
	prerelease = |version| {
		if version.to_utf8().len() > 100 {
			return Err("release version exceeds 100 ASCII characters")
		}
		parts = version.split_on("-")
		core = parts.first() ?? ""
		core_parts = core.split_on(".")
		if core_parts.len() != 3 or !core_parts.all(numeric_identifier) {
			return Err("release version requires three canonical numeric core identifiers")
		}
		if parts.len() == 1 {
			return Ok(Bool.False)
		}
		identifiers = version.drop_prefix("${core}-").split_on(".")
		if !identifiers.all(
			|identifier| {
				bytes = identifier.to_utf8()
				!bytes.is_empty() and bytes.all(|byte| (byte >= '0' and byte <= '9') or (byte >= 'a' and byte <= 'z') or (byte >= 'A' and byte <= 'Z') or byte == '-') and (!digits(identifier) or numeric_identifier(identifier))
			},
		) {
			return Err("invalid SemVer prerelease identifier")
		}
		Ok(Bool.True)
	}
	valid = |version| prerelease(version).is_ok()
}

digits = |value| !value.is_empty() and value.to_utf8().all(|byte| byte >= '0' and byte <= '9')

numeric_identifier = |value| digits(value) and (value == "0" or !value.starts_with("0"))

cases : { valid : List(Str), invalid : List(Str) }
cases = Json.parse(cases_json) ?? crash "invalid shared release version test table"
expect cases.valid.all(ReleaseVersion.valid)
expect cases.invalid.all(|version| !ReleaseVersion.valid(version))
expect ReleaseVersion.prerelease("1.2.3-alpha-beta.1") == Ok(Bool.True)
expect ReleaseVersion.prerelease("1.2.3") == Ok(Bool.False)
