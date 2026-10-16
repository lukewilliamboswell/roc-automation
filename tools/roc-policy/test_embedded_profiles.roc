app [main!] {
	cli: platform "https://github.com/roc-lang/basic-cli/releases/download/0.25.0/CZsY7tYZwR3rj9kYbpaCfxki2yVAaRL8bBwMLvB2xkbA.tar.zst",
	parser: "https://github.com/lukewilliamboswell/roc-parser/releases/download/2.1.0-rc.1/6dvNG3Q2mx53i4YPJN2ZGK98WdXnBMMuceAmDsCq7BEd.tar.zst",
}

import src/EmbeddedProfiles
import src/ConstantData
import src/Sha256
main! = |_| Ok({})

expect EmbeddedProfiles.list == [{ name: "roc-package", version: 1, description: "Public documentation for package sources and spelling for Roc and prose files" }]
expect EmbeddedProfiles.get("roc-package", 0).is_err()
expect EmbeddedProfiles.get("roc-package", 2).is_err()
expect EmbeddedProfiles.get("unknown", 1).is_err()
expect EmbeddedProfiles.get("Roc-Package", 1).is_err()
expect {
	source = EmbeddedProfiles.get("roc-package", 1)?
	value = ConstantData.evaluate(source, "profile")?
	match ConstantData.unwrap(value).kind {
		Record(_) => True
		_ => False
	}
}
# Immutable profile identity includes exact embedded bytes. Compute independently
# using Python hashlib when deliberately adding a new version.
expect Sha256.hex(EmbeddedProfiles.get("roc-package", 1)?.to_utf8()) == "8e3a8ce65a914c0f882b1000d0dcaae8a645dac0242fc3432a4b3a86229308b3"
