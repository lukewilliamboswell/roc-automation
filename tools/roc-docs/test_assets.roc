app [main!] {
	cli: platform "https://github.com/roc-lang/basic-cli/releases/download/0.26.0-rc2/Gj3zVDr3qrQa6aTh7Z1zgUfe9NeBE6R76T5ho3xrc8af.tar.zst",
	parser: "https://github.com/lukewilliamboswell/roc-parser/releases/download/2.1.0-rc.2/CZPwnt8ziVkc4v27SX7y2c8shxvPToiXiU1Y55E8tobb.tar.zst",
}

import ImageAssets

main! = |_args| Ok({})

expect ImageAssets.fit([255, 0, 0], 1, 1, 2, 2) == Ok({ width: 1, height: 1, pixels: [255, 0, 0] })
expect ImageAssets.fit([255, 0, 0, 0, 255, 0, 0, 0, 255, 255, 255, 255], 2, 2, 1, 1) == Ok({ width: 1, height: 1, pixels: [127, 127, 127] })
expect ImageAssets.fit([], 1, 1, 2, 2) == Err(InvalidRaster)
expect ImageAssets.fit([255, 0, 0], 0, 1, 2, 2) == Err(InvalidRaster)
expect ImageAssets.fit([0, 0, 0, 100, 100, 100, 200, 200, 200], 3, 1, 2, 2) == Ok({ width: 2, height: 1, pixels: [0, 0, 0, 150, 150, 150] })
expect ImageAssets.fit([1, 2, 3, 4, 5, 6], 2, 1, 1, 1) == Ok({ width: 1, height: 1, pixels: [2, 3, 4] })
expect ImageAssets.fit([1, 2, 3, 4, 5, 6], 1, 2, 1, 1) == Ok({ width: 1, height: 1, pixels: [2, 3, 4] })
expect ImageAssets.fit([], 8193, 1, 1, 1) == Err(InvalidRaster)
expect ImageAssets.fit([1, 2, 3], 1, 1, 0, 1) == Err(InvalidRaster)
