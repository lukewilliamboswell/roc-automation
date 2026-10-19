## PDF destination names are printable ASCII. Encode UTF-8 bytes injectively,
## including '%' itself, so Unicode IDs cannot collide with literal escapes.
Destination :: [].{
	encode : Str -> Str
	encode = |name| Str.join_with(
		name.to_utf8().map(
			|byte| {
				if (byte >= 'a' and byte <= 'z') or (byte >= 'A' and byte <= 'Z') or (byte >= '0' and byte <= '9') or byte == '-' or byte == '_' or byte == '.' {
					Str.from_utf8_lossy([byte])
				} else {
					hex = "0123456789ABCDEF".to_utf8()
					Str.from_utf8_lossy(['%', hex.get((byte // 16).to_u64()) ?? '0', hex.get((byte % 16).to_u64()) ?? '0'])
				}
			},
		),
		"",
	)
}
