## SemVer precedence for authored-note selection, without integer-size limits.
## Callers admit version spelling with the shared lifecycle version validator.
ReleaseMetadata := [].{
	compare = |a, b| {
		ac = a.split_on("-").first() ?? a
		bc = b.split_on("-").first() ?? b
		for i in 0..<3 {
			decision = numeric(ac.split_on(".").get(i) ?? "0", bc.split_on(".").get(i) ?? "0")
			if decision != Same {
				return decision
			}
		}
		if a == ac and b == bc {
			return Same
		}
		if a == ac {
			return After
		}
		if b == bc {
			return Before
		}
		ap = a.drop_prefix("${ac}-").split_on(".")
		bp = b.drop_prefix("${bc}-").split_on(".")
		for i in 0..<(if ap.len() < bp.len() ap.len() else bp.len()) {
			x = ap.get(i) ?? ""
			y = bp.get(i) ?? ""
			xn = digits(x)
			yn = digits(y)
			decision = if xn and yn numeric(x, y) else if xn Before else if yn After else lexical(x, y)
			if decision != Same {
				return decision
			}
		}
		if ap.len() < bp.len() Before else if ap.len() > bp.len() After else Same
	}
}

digits = |text| !text.is_empty() and text.to_utf8().all(|b| b >= '0' and b <= '9')

numeric = |a, b| if a.to_utf8().len() < b.to_utf8().len() Before else if a.to_utf8().len() > b.to_utf8().len() After else lexical(a, b)

lexical = |a, b| {
	left = a.to_utf8()
	right = b.to_utf8()
	for i in 0..<(if left.len() < right.len() left.len() else right.len()) {
		x = left.get(i) ?? 0
		y = right.get(i) ?? 0
		if x < y {
			return Before
		}
		if x > y {
			return After
		}
	}
	if left.len() < right.len() Before else if left.len() > right.len() After else Same
}

expect ReleaseMetadata.compare("9.0.0", "10.0.0") == Before
expect ReleaseMetadata.compare("1.0.0-rc.9", "1.0.0-rc.10") == Before
expect ReleaseMetadata.compare("1.0.0", "1.0.0-rc.10") == After
expect ReleaseMetadata.compare("1.0.0-alpha", "1.0.0-alpha.1") == Before
expect ReleaseMetadata.compare("1.0.0-1", "1.0.0-alpha") == Before
expect ReleaseMetadata.compare("1.0.0-alpha-beta", "1.0.0-alpha-beta") == Same
expect ReleaseMetadata.compare("9999999999999999999999999.0.0", "10000000000000000000000000.0.0") == Before
