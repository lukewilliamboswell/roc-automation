## Deterministic prepared-RGB adaptation, independent of PDF/HTML/platform code.
## Original PNG bytes remain unchanged for the website. PDF rasters need only
## enough pixels for their physical placement; oversized originals waste the
## PDF package's bounded resource budget without adding visible detail.
ImageAssets :: [].{
	fit : List(U8), U64, U64, U64, U64 -> Try({ width : U64, height : U64, pixels : List(U8) }, [InvalidRaster])
	fit = |pixels, width, height, max_width, max_height| {
		if width == 0 or height == 0 or width > 8192 or height > 8192 or max_width == 0 or max_height == 0 or pixels.len() != width * height * 3 {
			return Err(InvalidRaster)
		}
		width_fit = if width > max_width max_width else width
		height_floor = height * width_fit // width
		height_fit = if height_floor == 0 1 else height_floor
		final_height = if height_fit > max_height max_height else height_fit
		final_width = width_fit * final_height // height_fit
		target_width = if final_width == 0 1 else final_width
		target_height = if final_height == 0 1 else final_height
		if target_width == width and target_height == height {
			return Ok({ width, height, pixels })
		}
		var $out = []
		for y in 0..<target_height {
			top = y * height // target_height
			bottom = (y + 1) * height // target_height
			for x in 0..<target_width {
				left = x * width // target_width
				right = (x + 1) * width // target_width
				count = (right - left) * (bottom - top)
				var $red = 0.U64
				var $green = 0.U64
				var $blue = 0.U64
				for source_y in top..<bottom {
					for source_x in left..<right {
						offset = (source_y * width + source_x) * 3
						$red = $red + byte_at(pixels, offset).to_u64()
						$green = $green + byte_at(pixels, offset + 1).to_u64()
						$blue = $blue + byte_at(pixels, offset + 2).to_u64()
					}
				}
				$out = $out.append(($red // count).to_u8_wrap()).append(($green // count).to_u8_wrap()).append(($blue // count).to_u8_wrap())
			}
		}
		Ok({ width: target_width, height: target_height, pixels: $out })
	}
}

byte_at = |bytes, index| match bytes.get(index) {
	Ok(byte) => byte
	Err(_) => 0
}
