import "../vendor/typos/words.csv" as dictionary : Str
import "../vendor/typos/LICENSE-MIT" as mit_notice : Str

## Pinned typos correction map. See vendor/typos/PROVENANCE.adoc and LICENSE-MIT.
## Lookup searches embedded UTF-8 bytes; it does not load files or split the
## entire dictionary for each word. Keys are folded by the calling text policy.
TypoData :: [].{

	## Upstream permission notice for binary distribution and --licenses output.
	license : Str
	license = mit_notice

	## All upstream suggestions, in their original order. Empty means unknown.
	suggestions : Str -> List(Str)
	suggestions = |word| {
		bytes = dictionary.to_utf8()
		key = word.to_utf8()
		var $low = 0
		var $high = bytes.len()
		while $low < $high {
			# Locate the start of the record containing the midpoint.
			var $start = $low + ($high - $low) // 2
			while $start > $low and (bytes.get($start - 1) ?? 0) != '\n' {
				$start = $start - 1
			}
			var $separator = $start
			while $separator < bytes.len() and (bytes.get($separator) ?? 0) != ',' {
				$separator = $separator + 1
			}
			candidate = bytes.sublist({ start: $start, len: $separator - $start })
			ordering = compare_bytes(key, candidate)
			if ordering == Eq {
				var $end = $separator + 1
				while $end < bytes.len() and (bytes.get($end) ?? 0) != '\n' {
					$end = $end + 1
				}
				return Str.from_utf8_lossy(bytes.sublist({ start: $separator + 1, len: $end - $separator - 1 })).split_on(",")
			} else if ordering == Lt {
				$high = $start
			} else {
				var $end = $separator + 1
				while $end < bytes.len() and (bytes.get($end) ?? 0) != '\n' {
					$end = $end + 1
				}
				$low = if $end < bytes.len() $end + 1 else bytes.len()
			}
		}
		[]
	}
}

compare_bytes : List(U8), List(U8) -> [Lt, Eq, Gt]
compare_bytes = |left, right| {
	var $i = 0
	while $i < left.len() and $i < right.len() {
		a = left.get($i) ?? 0
		b = right.get($i) ?? 0
		if a < b {
			return Lt
		}
		if a > b {
			return Gt
		}
		$i = $i + 1
	}
	if left.len() < right.len() Lt else if left.len() > right.len() Gt else Eq
}
