import Compatibility

## Deterministic ZIP/STORE encoding: sorted canonical regular paths, fixed
## timestamps and Unix permissions, no executable entries or environment data.
## Extraction remains the independent, bounded shared archive helper's job.
ReleaseArchive := [].{
	Entry : { path : Str, bytes : List(U8) }
	pack : List(Entry) -> Try(List(U8), Str)
	pack = |entries| {
		if entries.is_empty() or entries.len() > 4096 or entries.any(|e| !Compatibility.path(e.path) or e.bytes.len() > 50_000_000) {
			return Err("ZIP needs 1–4096 bounded canonical regular entries")
		}
		if Compatibility.unique(entries.map(|e| e.path)).len() != entries.len() {
			return Err("duplicate ZIP path")
		}
		ordered = entries.sort_with(|a, b| compare(a.path, b.path))
		var $local = []
		var $central = []
		for entry in ordered {
			name = entry.path.to_utf8()
			if name.len() > 4096 or $local.len() + entry.bytes.len() > 50_000_000 {
				return Err("ZIP exceeds byte or path limit")
			}
			size = le32(entry.bytes.len())
			crc = le32(crc32(entry.bytes).to_u64())
			length = le16(name.len())
			header = [80, 75, 3, 4, 20, 0, 0, 0, 0, 0, 0, 0, 33, 0].concat(crc).concat(size).concat(size).concat(length).concat([0, 0]).concat(name)
			central = [80, 75, 1, 2, 20, 3, 20, 0, 0, 0, 0, 0, 0, 0, 33, 0].concat(crc).concat(size).concat(size).concat(length).concat([0, 0, 0, 0, 0, 0, 0, 0]).concat(le32(2175008768)).concat(le32($local.len())).concat(name)
			$central = $central.concat(central)
			$local = $local.concat(header).concat(entry.bytes)
		}
		end = [80, 75, 5, 6, 0, 0, 0, 0].concat(le16(entries.len())).concat(le16(entries.len())).concat(le32($central.len())).concat(le32($local.len())).concat([0, 0])
		result = $local.concat($central).concat(end)
		if result.len() > 50_000_000 Err("ZIP exceeds byte limit") else Ok(result)
	}
	crc32 = |bytes| {
		initial : U32
		initial = 0xffffffff
		bytes.fold(initial, |crc, byte| {
			var $crc = crc.bitwise_xor(byte.to_u32())
			for _ in 0..<8 {
				$crc = if $crc.bitwise_and(1) == 1 $crc.shr_wrap(1).bitwise_xor(0xedb88320) else $crc.shr_wrap(1)
			}
			$crc
		}).bitwise_xor(0xffffffff)
	}
	compare = compare
}

le16 : U64 -> List(U8)
le16 = |n| [n.to_u8_wrap(), n.shr_wrap(8).to_u8_wrap()]
le32 : U64 -> List(U8)
le32 = |n| le16(n).concat(le16(n.shr_wrap(16)))
compare = |a, b| {
	left = a.to_utf8()
	right = b.to_utf8()
	for i in 0..<(if left.len() < right.len() left.len() else right.len()) {
		x = left.get(i) ?? 0
		y = right.get(i) ?? 0
		if x < y { return Before }
		if x > y { return After }
	}
	if left.len() < right.len() Before else if left.len() > right.len() After else Same
}

expect ReleaseArchive.crc32("123456789".to_utf8()) == 0xcbf43926
expect ReleaseArchive.pack([{ path: "root/b", bytes: [2] }, { path: "root/a", bytes: [1] }]) == ReleaseArchive.pack([{ path: "root/a", bytes: [1] }, { path: "root/b", bytes: [2] }])
expect ReleaseArchive.pack([{ path: "../escape", bytes: [] }]).is_err()
expect ReleaseArchive.pack([{ path: "root/a", bytes: [] }, { path: "root/a", bytes: [] }]).is_err()
