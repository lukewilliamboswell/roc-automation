import parser.Yaml
import SyntaxHighlight

## Original-source foreground ranges shared by HTML and PDF. Roc and YAML use
## upstream lexers. Small lexical adapters cover shell, JSON and unified diffs;
## none of these highlighters requires the snippet to be a valid program.
CodeHighlight :: [].{
	highlight : Str, Str -> List(SyntaxHighlight.Span)
	highlight = |language, source| {
		if language == "roc" {
			return SyntaxHighlight.highlight(source)
		}
		if language == "yaml" or language == "yml" {
			lexed = Yaml.tokenize_str(source)
			var $cursor = 0.U64
			var $spans = []
			for token in lexed.tokens {
				if token.start != $cursor or token.end <= token.start or token.end > lexed.source.len() or Str.from_utf8(lexed.source.sublist({ start: token.start, len: token.end - token.start })).is_err() {
					return plain(source)
				}
				$cursor = token.end
				category = match Yaml.highlight_category(token) {
					PropertyName => Name
					BooleanLiteral | NullLiteral => ReservedWord
					Name => Name
					Trivia => Trivia
					CommentText => CommentText
					StringText => StringText
					NumericLiteral => NumericLiteral
					Symbol => Symbol
					TypeName => TypeName
					InvalidText => InvalidText
				}
				$spans = add($spans, token.start, token.end, category)
			}
			return if $cursor == lexed.source.len() $spans else plain(source)
		}
		shell = ["sh", "bash", "shell", "zsh"].contains(language)
		if !shell and language != "json" and language != "diff" and language != "patch" {
			return plain(source)
		}
		var $result = []
		var $offset = 0.U64
		for line in source.split_on("\n") {
			bytes = line.to_utf8()
			if language == "diff" or language == "patch" {
				category = if line.starts_with("@@") or line.starts_with("diff ") Name else if line.starts_with("+") StringText else if line.starts_with("-") InvalidText else Trivia
				$result = add($result, $offset, $offset + bytes.len(), category)
			} else {
				var $cursor = 0.U64
				while $cursor < bytes.len() {
					start = $cursor
					byte = at(bytes, start)
					var $category = Symbol
					$cursor = $cursor + 1
					if whitespace(byte) {
						$category = Trivia
					} else if byte == '#' and shell {
						$category = CommentText
						$cursor = bytes.len()
					} else if byte == '"' or (shell and byte == '\'') {
						$category = StringText
						var $closed = False
						while $cursor < bytes.len() and !$closed {
							current = at(bytes, $cursor)
							$cursor = $cursor + 1
							if current == '\\' and byte == '"' and $cursor < bytes.len() {
								$cursor = $cursor + 1
							} else if current == byte {
								$closed = True
							}
						}
						if !shell and key_follows(bytes, $cursor) {
							$category = Name
						}
					} else if !punctuation(byte, shell) {
						while $cursor < bytes.len() and !punctuation(at(bytes, $cursor), shell) and !whitespace(at(bytes, $cursor)) {
							$cursor = $cursor + 1
						}
						value = Str.from_utf8_lossy(bytes.sublist({ start, len: $cursor - start }))
						$category = if (shell and ["if", "then", "else", "elif", "fi", "for", "in", "do", "done", "case", "esac", "while", "export", "set"].contains(value)) or (!shell and ["true", "false", "null"].contains(value)) ReservedWord else if F64.from_str(value).is_ok() NumericLiteral else Name
					}
					$result = add($result, $offset + start, $offset + $cursor, $category)
				}
			}
			$offset = $offset + bytes.len()
			if $offset < source.to_utf8().len() {
				$result = add($result, $offset, $offset + 1, Trivia)
				$offset = $offset + 1
			}
		}
		$result
	}
}

plain = |source| if source.is_empty() [] else [{ start: 0, end: source.to_utf8().len(), category: Trivia }]

at = |bytes, index| bytes.get(index) ?? 0

whitespace = |byte| byte == ' ' or byte == '\t' or byte == '\r'

punctuation = |byte, shell| if shell [124, 38, 59, 40, 41, 60, 62, 61].contains(byte) else [91, 93, 123, 125, 44, 58].contains(byte)

key_follows = |bytes, index| {
	var $next = index
	while $next < bytes.len() and whitespace(at(bytes, $next)) {
		$next = $next + 1
	}
	$next < bytes.len() and at(bytes, $next) == ':'
}

add = |spans, start, end, category| {
	if start == end {
		return spans
	}
	match spans.last() {
		Ok(previous) if previous.category == category => spans.drop_last(1).append({ ..previous, end })
		_ => spans.append({ start, end, category })
	}
}
