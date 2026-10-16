app [main!] {
	cli: platform "https://github.com/roc-lang/basic-cli/releases/download/0.25.0/CZsY7tYZwR3rj9kYbpaCfxki2yVAaRL8bBwMLvB2xkbA.tar.zst",
}

import src/TypoData
import "vendor/typos/words.csv" as snapshot : Str

# Exercise first/last records, Unicode, all suggestions, and lookup misses.
expect TypoData.suggestions("aaccess") == ["access"]
expect TypoData.suggestions("aache") == ["ache", "cache"]
expect TypoData.suggestions("teh") == ["the"]
expect TypoData.suggestions("сontains") == ["contains"]
expect TypoData.suggestions("").is_empty()
expect TypoData.suggestions("zzzzzzzzzzzzzzzzzz").is_empty()
expect TypoData.suggestions("inventedrocword").is_empty()
expect {
	rows = snapshot.split_on("\n").keep_if(|row| !row.is_empty())
	rows.len() == 93861 and rows.all(
		|row| {
			fields = row.split_on(",")
			key = fields.first() ?? ""
			TypoData.suggestions(key) == fields.drop_first(1)
		},
	)
}

main! = |_args| Ok({})
