import font.Font

## Public roc-font defaults, shared by the renderer and standalone website assets.
WebAssets :: [].{
	list : List({ path : Str, bytes : List(U8) })
	list = [
		{ path: "_roc-docs/fonts/Inter-Regular.ttf", bytes: Font.sans_bytes },
		{ path: "_roc-docs/fonts/Inter-Bold.ttf", bytes: Font.sans_bold_bytes },
		{ path: "_roc-docs/fonts/Inter-Italic.ttf", bytes: Font.sans_italic_bytes },
		{ path: "_roc-docs/fonts/Inter-BoldItalic.ttf", bytes: Font.sans_bold_italic_bytes },
		{ path: "_roc-docs/fonts/Merriweather-Regular.ttf", bytes: Font.serif_bytes },
		{ path: "_roc-docs/fonts/Merriweather-Bold.ttf", bytes: Font.serif_bold_bytes },
		{ path: "_roc-docs/fonts/Merriweather-Italic.ttf", bytes: Font.serif_italic_bytes },
		{ path: "_roc-docs/fonts/Merriweather-BoldItalic.ttf", bytes: Font.serif_bold_italic_bytes },
		{ path: "_roc-docs/fonts/FiraCode-Regular.ttf", bytes: Font.mono_bytes },
		{ path: "_roc-docs/fonts/FiraCode-Bold.ttf", bytes: Font.mono_bold_bytes },
		{ path: "_roc-docs/fonts/Inter-OFL.txt", bytes: Font.sans_licence.to_utf8() },
		{ path: "_roc-docs/fonts/Merriweather-OFL.txt", bytes: Font.serif_licence.to_utf8() },
		{ path: "_roc-docs/fonts/FiraCode-OFL.txt", bytes: Font.mono_licence.to_utf8() },
	]
}
