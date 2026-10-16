import parser.RocSyntax
import TypoData

## Text policies preserve half-open UTF-8 byte spans. ASCII case folding only.
## Spelling is a correction map, not unknown-word detection. Phrase matching is
## case-insensitive and whole-word; whitespace runs normalize, punctuation
## must match exactly. Identifiers split snake/camel/acronyms/digits.
TextPolicy :: [].{
	Channel : [Comments, Identifiers, Strings, Prose]

	## Spelling uses bundled corrections plus words (custom entries override).
	## Nonempty forbidden selects a single custom correction instead. allow
	## exempts folded whole words or entire identifiers; Phrase allow exempts
	## its exact folded configured phrase. No banned phrases are enabled here.
	Rule : { kind : [Spelling, Phrase], channels : List(Channel), forbidden : Str, replacement : Str, allow : List(Str), words : List((Str, Str)) }
	Issue : { rule : Str, message : Str, start : U64, end : U64 }

	## Check selected Roc lexical channels. Callers must reject incomplete parses.
	## Strings check raw StringPart bytes; escapes are not evaluated. Comments
	## may span consecutive lines. Prose is intentionally ignored in Roc files.
	check_roc : RocSyntax.Parsed, Rule -> List(Issue)
	check_roc = |p, rule| {
		var $out = []
		var $comment_start = 0
		var $comments = []
		for token in p.tokens {
			if token.kind == Comment and rule.channels.contains(Comments) {
				if $comments.is_empty() {
					$comment_start = token.start
				}
				comment_bytes = p.source.sublist({ start: token.start, len: token.end - token.start })
				var $prefix = 0
				while byte(comment_bytes, $prefix) == '#' {
					$prefix = $prefix + 1
				}
				$comments = $comments.concat(comment_bytes.map_with_index(|b, index| if index < $prefix ' ' else b))
			} else if token.kind == Whitespace and !$comments.is_empty() {
				$comments = $comments.concat(p.source.sublist({ start: token.start, len: token.end - token.start }))
			} else {
				if !$comments.is_empty() {
					$out = $out.concat(scan($comments, $comment_start, False, rule))
					$comments = []
				}
				identifier = token.kind == LowerIdent or token.kind == UpperIdent
				if (identifier and rule.channels.contains(Identifiers)) or (token.kind == StringPart and rule.channels.contains(Strings)) {
					$out = $out.concat(scan(p.source.sublist({ start: token.start, len: token.end - token.start }), token.start, identifier, rule))
				}
			}
		}
		if !$comments.is_empty() {
			$out = $out.concat(scan($comments, $comment_start, False, rule))
		}
		$out
	}

	## Check prose bytes without requiring Roc syntax (Markdown, AsciiDoc, etc.).
	check_text : List(U8), Rule -> List(Issue)
	check_text = |source, rule| if rule.channels.contains(Prose) scan(source, 0, False, rule) else []
}

Word : { text : Str, start : U64, end : U64 }

lower_byte = |b| if b >= 'A' and b <= 'Z' b + 32 else b

fold = |text| Str.from_utf8_lossy(text.to_utf8().map(lower_byte))

upper = |b| b >= 'A' and b <= 'Z'

lower = |b| b >= 'a' and b <= 'z'

letter = |b| upper(b) or lower(b) or b >= 128

space = |b| b == ' ' or b == '\t' or b == '\r' or b == '\n'

byte = |source, at| source.get(at) ?? 0

words : List(U8), Bool -> List(Word)
words = |source, identifier| {
	var $out = []
	var $i = 0
	while $i < source.len() {
		if !letter(byte(source, $i)) {
			$i = $i + 1
		} else {
			start = $i
			$i = $i + 1
			while $i < source.len() and letter(byte(source, $i)) {
				current = byte(source, $i)
				previous = byte(source, $i - 1)
				if identifier and upper(current) and (lower(previous) or (upper(previous) and lower(byte(source, $i + 1)))) {
					break
				}
				$i = $i + 1
			}
			$out = $out.append({ text: fold(Str.from_utf8_lossy(source.sublist({ start, len: $i - start }))), start, end: $i })
		}
	}
	$out
}

scan : List(U8), U64, Bool, TextPolicy.Rule -> List(TextPolicy.Issue)
scan = |source, offset, identifier, rule| {
	items = words(source, identifier)
	allowed = rule.allow.map(fold)
	var $out = []
	if rule.kind == Spelling {
		# A whole identifier glossary entry suppresses its component words too.
		if identifier and allowed.contains(fold(Str.from_utf8_lossy(source))) {
			return []
		}
		custom = rule.words.map(|(bad, replacement)| (fold(bad), replacement))
		for item in items {
			if !allowed.contains(item.text) {
				# Explicit single-word rules and repository overrides take precedence.
				suggestions = if !rule.forbidden.is_empty() {
					if item.text == fold(rule.forbidden) [rule.replacement] else []
				} else {
					overrides = custom.keep_if(|(key, _)| key == item.text).map(|(_, value)| value)
					if overrides.is_empty() TypoData.suggestions(item.text) else overrides
				}
				if !suggestions.is_empty() {
					replacement = Str.join_with(suggestions, "' or '")
					$out = $out.append({ rule: "text/spelling", message: "possible misspelling '${item.text}'; use '${replacement}'", start: offset + item.start, end: offset + item.end })
				}
			}
		}
	} else {
		needle_source = rule.forbidden.to_utf8()
		needle = words(needle_source, False)
		if needle.is_empty() or allowed.contains(fold(rule.forbidden)) {
			return []
		}
		var $i = 0
		while $i + needle.len() <= items.len() {
			var $matches = True
			var $j = 0
			while $j < needle.len() {
				item = items.get($i + $j) ?? { text: "", start: 0, end: 0 }
				expected = needle.get($j) ?? { text: "", start: 0, end: 0 }
				if item.text != expected.text {
					$matches = False
				}
				if $j > 0 {
					previous = items.get($i + $j - 1) ?? item
					previous_expected = needle.get($j - 1) ?? expected
					gap = source.sublist({ start: previous.end, len: item.start - previous.end })
					expected_gap = needle_source.sublist({ start: previous_expected.end, len: expected.start - previous_expected.end })
					if !(gap.all(space) and expected_gap.all(space)) and gap != expected_gap {
						$matches = False
					}
				}
				$j = $j + 1
			}
			if $matches {
				first = items.get($i) ?? { text: "", start: 0, end: 0 }
				last = items.get($i + needle.len() - 1) ?? first
				needle_first = needle.first() ?? first
				needle_last = needle.last() ?? last
				prefix_len = needle_first.start
				suffix_len = needle_source.len() - needle_last.end
				if first.start < prefix_len or last.end + suffix_len > source.len() {
					$matches = False
				} else {
					if source.sublist({ start: first.start - prefix_len, len: prefix_len }) != needle_source.sublist({ start: 0, len: prefix_len }) {
						$matches = False
					}
					if source.sublist({ start: last.end, len: suffix_len }) != needle_source.sublist({ start: needle_last.end, len: suffix_len }) {
						$matches = False
					}
				}
				if $matches {
					message = if rule.replacement.is_empty() "avoid phrase '${rule.forbidden}'" else "replace '${rule.forbidden}' with '${rule.replacement}'"
					$out = $out.append({ rule: "text/phrase", message, start: offset + first.start - prefix_len, end: offset + last.end + suffix_len })
				}
			}
			$i = $i + 1
		}
	}
	$out
}
