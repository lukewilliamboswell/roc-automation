import "../profiles/roc-package-v1.roc" as roc_package_v1 : Str

## Immutable profile data shipped in the executable, with no runtime file reads.
EmbeddedProfiles :: [].{

	## Resolve an explicit name and version. Unknown identities fail closed.
	get : Str, U64 -> Try(Str, Str)
	get = |name, version| {
		if name == "roc-package" and version == 1 {
			Ok(roc_package_v1)
		} else {
			Err("Unknown embedded profile '${name}' version ${version.to_str()}")
		}
	}

	## Available profile identities and their intended use.
	list : List({ name : Str, version : U64, description : Str })
	list = [
		{
			name: "roc-package",
			version: 1,
			description: "Public documentation for package sources and spelling for Roc and prose files",
		},
	]
}
