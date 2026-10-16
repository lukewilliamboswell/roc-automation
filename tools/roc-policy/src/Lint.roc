import parser.RocSyntax
import ConstantData
import PolicyConfig
import TextPolicy
import PublicDocs

## Read-only source checks; the caller controls discovery and presentation.
Lint :: [].{
	Issue : { rule : Str, message : Str, start : U64, end : U64 }

	## An exact entrypoint scope requires a header. Directory scopes apply
	## required-header rules only to app/package/platform headers.
	eligible = |path, source, rule| {
		if path != rule.scope and !path.starts_with("${rule.scope}/") {
			return False
		}
		if !path.ends_with(".roc") {
			return (rule.kind == Spelling or rule.kind == Phrase) and rule.channels.contains(Prose)
		}
		if rule.kind == Spelling or rule.kind == Phrase {
			return rule.channels.any(|channel| channel != Prose)
		}
		if rule.kind != Dependency and rule.kind != CompilerPin {
			return True
		}
		if path == rule.scope {
			return True
		}
		p = RocSyntax.parse_bytes(source)
		if !complete(p) {
			return True
		}
		p.nodes.any(|n| dependency_header(p, n))
	}
	check : Str, List(U8), List(PolicyConfig.Rule) -> List(Issue)
	check = |path, source, rules| {
		applicable = rules.keep_if(|rule| path == rule.scope or path.starts_with("${rule.scope}/"))
		if applicable.is_empty() {
			return []
		}
		if !path.ends_with(".roc") {
			if Str.from_utf8(source).is_err() {
				return [{ rule: "syntax/incomplete", message: "invalid UTF-8 text", start: 0, end: source.len() }]
			}
			var $text_issues = []
			for rule in applicable {
				if rule.kind == Spelling or rule.kind == Phrase {
					$text_issues = $text_issues.concat(TextPolicy.check_text(source, text_rule(rule)))
				}
			}
			return $text_issues
		}
		p = RocSyntax.parse_bytes(source)
		if !complete(p) {
			at = (p.diagnostics.first() ?? { code: UnexpectedToken, start: 0, end: 0 }).start
			return [issue(p, at, "syntax/incomplete", "analysis incomplete: ${Str.inspect(p.status)}")]
		}
		var $issues = []
		for rule in applicable {
			if rule.kind == PublicDocs {
				$issues = $issues.concat(PublicDocs.check(path, p))
			} else if rule.kind == Spelling or rule.kind == Phrase {
				$issues = $issues.concat(TextPolicy.check_roc(p, text_rule(rule)))
			} else if rule.kind == ForbiddenImport {
				for imported in p.nodes.keep_if(|n| n.kind == ImportPath) {
					# The grammar accepts trivia between some path components.
					# Compare significant tokens, retaining the original span.
					spelling = Str.join_with(p.tokens.keep_if(|t| t.start >= imported.start and t.end <= imported.end and t.kind != Whitespace and t.kind != Comment).map(|t| Str.from_utf8_lossy(p.source.sublist({ start: t.start, len: t.end - t.start }))), "")
					if spelling == rule.name or spelling.starts_with("${rule.name}.") or spelling.starts_with("${rule.name}/") {
						$issues = $issues.append(issue(p, imported.start, "architecture/import-boundary", "import ${spelling} is forbidden in ${rule.scope}"))
					}
				}
			} else {
				headers = p.nodes.keep_if(|n| dependency_header(p, n))
				if headers.is_empty() and path == rule.scope and rule.kind != NoCompilerPin {
					$issues = $issues.append(issue(p, 0, "project/header", "required app, package or platform header is missing"))
				}
				if !headers.is_empty() {
					fields = p.nodes.keep_if(|n| n.kind == DependencyField).keep_if(|n| text(p, children(p, n).first() ?? n) == rule.name)
					if rule.kind == NoCompilerPin {
						for field in fields {
							$issues = $issues.append(issue(p, field.start, "project/compiler-pin", "compiler pin forbidden in ${rule.scope}"))
						}
					} else {
						actual = fields.map(
							|field| {
								value = children(p, field).last() ?? field
								literal = if value.kind == PlatformReference children(p, value).first() ?? value else value
								string(p, literal)
							},
						)
						if actual != [Ok(rule.value)] {
							at = (fields.first() ?? (headers.first() ?? node(p, p.root))).start
							id = if rule.kind == Dependency "project/dependency" else "project/compiler-pin"
							$issues = $issues.append(issue(p, at, id, "expected exactly one ${rule.name}: ${Str.inspect(rule.value)}, found ${Str.inspect(actual)}"))
						}
					}
				}
			}
		}
		$issues
	}
}

text_rule = |rule| { kind: if rule.kind == Spelling Spelling else Phrase, channels: rule.channels, forbidden: if rule.kind == Phrase rule.name else "", replacement: rule.value, allow: rule.allow, words: rule.words }

complete = |p| p.status == Complete and p.consumed and p.diagnostics.is_empty()

node = |p, index| p.nodes.get(index) ?? { kind: ErrorNode, start: 0, end: 0, children_start: 0, children_len: 0 }

children = |p, n| p.children.sublist({ start: n.children_start, len: n.children_len }).map(|index| node(p, index))

text = |p, n| Str.from_utf8_lossy(p.source.sublist({ start: n.start, len: n.end - n.start }))

dependency_header = |p, n| n.kind == Header and p.tokens.any(|t| t.start == n.start and ["app", "package", "platform"].contains(Str.from_utf8_lossy(p.source.sublist({ start: t.start, len: t.end - t.start }))))

string = |p, n| {
	value = ConstantData.evaluate("literal = ${text(p, n)}", "literal").map_err(|e| e.message)?
	match ConstantData.unwrap(value).kind {
		String(found) => Ok(found)
		_ => Err("dependency must be a string literal")
	}
}

issue = |_p, at, rule, message| { rule, message, start: at, end: at }
