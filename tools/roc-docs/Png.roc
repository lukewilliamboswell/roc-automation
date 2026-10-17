import BoundedInflate

## Bounded native PNG reader for the documented RGB/RGBA8 non - interlaced
## authoring subset. Checks chunk CRCs, zlib framing/Adler32 and exact pixels.
## Other encodings fail explicitly; alpha is composited onto white for PDF.
Png :: [].{
	Raster : { width : U64, height : U64, pixels : List(U8) }
	decode : List(U8) -> Try(Raster, Str)
	decode = |data| {
		if data.len() > 33554432 or data.take_first(8) != [137, 80, 78, 71, 13, 10, 26, 10] {
			return Err("PNG signature or input limit")
		}
		var $offset = 8.U64
		var $header = []
		var $compressed = []
		var $ended = False
		var $idat = False
		var $after_idat = False
		var $chunks = 0.U64
		while $offset < data.len() {
			$chunks = $chunks + 1
			if $chunks > 4096 or data.len() - $offset < 12 {
				return Err("PNG chunk limit or truncated header")
			}
			length = big(data, $offset)
			if length > data.len() - $offset - 12 {
				return Err("Truncated PNG chunk")
			}
			end = $offset + length + 12
			kind = data.sublist({ start: $offset + 4, len: 4 })
			if !kind.all(|b| (b >= 'A' and b <= 'Z') or (b >= 'a' and b <= 'z')) or byte(kind, 2).bitwise_and(32) != 0 {
				return Err("Invalid PNG chunk type")
			}
			payload = data.sublist({ start: $offset + 8, len: length })
			if crc(data.sublist({ start: $offset + 4, len: length + 4 })).to_u64() != big(data, end - 4) {
				return Err("PNG CRC mismatch")
			}
			if kind == [73, 72, 68, 82] {
				if !$header.is_empty() or $offset != 8 or length != 13 {
					return Err("Invalid PNG IHDR")
				}
				$header = payload
			} else if kind == [73, 68, 65, 84] {
				if $header.is_empty() or $after_idat {
					return Err("Invalid PNG IDAT order")
				}
				$idat = True
				$compressed = $compressed.concat(payload)
			} else if kind == [73, 69, 78, 68] {
				if length != 0 or end != data.len() or !$idat {
					return Err("Invalid PNG IEND")
				}
				$ended = True
			} else {
				if $idat {
					$after_idat = True
				}
				if kind == [116, 82, 78, 83] {
					return Err("PNG tRNS unsupported; use RGBA")
				}
				if byte(kind, 0).bitwise_and(32) == 0 {
					return Err("Unsupported PNG critical chunk")
				}
			}
			$offset = end
		}
		if $header.len() != 13 or !$ended {
			return Err("Incomplete PNG")
		}
		width = big($header, 0)
		height = big($header, 4)
		color = byte($header, 9)
		if width == 0 or height == 0 or width > 8192 or height > 8192 or width * height > 16000000 {
			return Err("PNG dimensions exceed limit")
		}
		if byte($header, 8) != 8 or (color != 2 and color != 6) or byte($header, 10) != 0 or byte($header, 11) != 0 or byte($header, 12) != 0 {
			return Err("PNG requires non - interlaced RGB/RGBA8")
		}
		channels = if color == 2 3.U64 else 4.U64
		stride = width * channels
		expected = height * (stride + 1)
		if $compressed.len() < 6 {
			return Err("Truncated PNG zlib stream")
		}
		cmf = byte($compressed, 0).to_u64()
		flags = byte($compressed, 1).to_u64()
		if cmf % 16 != 8 or cmf // 16 > 7 or (cmf * 256 + flags) % 31 != 0 or flags.bitwise_and(32) != 0 {
			return Err("Unsupported PNG zlib header")
		}
		raw = BoundedInflate.decompress($compressed.sublist({ start: 2, len: $compressed.len() - 6 }), expected).map_err(|_| "Invalid or oversized PNG DEFLATE stream")?
		if raw.len() != expected or adler(raw) != big($compressed, $compressed.len() - 4) {
			return Err("PNG pixel length or Adler32 mismatch")
		}
		var $previous = List.repeat(0.U8, stride)
		var $pixels = []
		for y in 0..<height {
			method = byte(raw, y * (stride + 1))
			if method > 4 {
				return Err("Unsupported PNG row filter")
			}
			var $scan = []
			for x in 0..<stride {
				encoded = byte(raw, y * (stride + 1) + 1 + x).to_u64()
				left = if x >= channels byte($scan, x - channels).to_u64() else 0
				up = byte($previous, x).to_u64()
				corner = if x >= channels byte($previous, x - channels).to_u64() else 0
				predictor = match method {
					0 => 0
					1 => left
					2 => up
					3 => (left + up) // 2
					_ => paeth(left, up, corner)
				}
				$scan = $scan.append(((encoded + predictor) % 256).to_u8_wrap())
			}
			if color == 2 {
				$pixels = $pixels.concat($scan)
			} else {
				for x in 0..<width {
					alpha = byte($scan, x * 4 + 3).to_u64()
					for channel in 0..<3.U64 {
						$pixels = $pixels.append(((byte($scan, x * 4 + channel).to_u64() * alpha + 255 * (255 - alpha) + 127) // 255).to_u8_wrap())
					}
				}
			}
			$previous = $scan
		}
		Ok({ width, height, pixels: $pixels })
	}
}

byte : List(U8), U64 -> U8
byte = |data, index| data.get(index) ?? 0

big = |data, index| byte(data, index).to_u64() * 16777216 + byte(data, index + 1).to_u64() * 65536 + byte(data, index + 2).to_u64() * 256 + byte(data, index + 3).to_u64()

crc = |data| {
	var $crc = 0xFFFFFFFF.U32
	for item in data {
		$crc = $crc.bitwise_xor(item.to_u32())
		for _bit in 0..<8.U64 {
			$crc = if $crc.bitwise_and(1) == 1 $crc.shr_zf_wrap(1).bitwise_xor(0xEDB88320) else $crc.shr_zf_wrap(1)
		}
	}
	$crc.bitwise_xor(0xFFFFFFFF)
}

adler = |data| {
	var $a = 1.U64
	var $b = 0.U64
	for item in data {
		$a = ($a + item.to_u64()) % 65521
		$b = ($b + $a) % 65521
	}
	$b * 65536 + $a
}

paeth = |left, up, corner| {
	p = left.to_i64_wrap() + up.to_i64_wrap() - corner.to_i64_wrap()
	a = p - left.to_i64_wrap()
	b = p - up.to_i64_wrap()
	c = p - corner.to_i64_wrap()
	da = if a < 0 0 - a else a
	db = if b < 0 0 - b else b
	dc = if c < 0 0 - c else c
	if da <= db and da <= dc left else if db <= dc up else corner
}
