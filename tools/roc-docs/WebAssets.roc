import "fonts/web/PLUS-JAKARTA-SANS-OFL.txt" as file_0 : List(U8)
import "fonts/web/PlusJakartaSans-Bold.ttf" as file_1 : List(U8)
import "fonts/web/PlusJakartaSans-BoldItalic.ttf" as file_2 : List(U8)
import "fonts/web/PlusJakartaSans-Italic.ttf" as file_3 : List(U8)
import "fonts/web/PlusJakartaSans-Regular.ttf" as file_4 : List(U8)
import "fonts/web/SPACE-GROTESK-OFL.txt" as file_5 : List(U8)
import "fonts/web/SpaceGrotesk-Bold.ttf" as file_6 : List(U8)
import "fonts/web/SpaceGrotesk-Regular.ttf" as file_7 : List(U8)

## Licensed reading fonts, embedded once in the binary and shared by site pages.
WebAssets :: [].{
	list : List({ path : Str, bytes : List(U8) })
	list = [{ path: "_roc-docs/fonts/PLUS-JAKARTA-SANS-OFL.txt", bytes: file_0 }, { path: "_roc-docs/fonts/PlusJakartaSans-Bold.ttf", bytes: file_1 }, { path: "_roc-docs/fonts/PlusJakartaSans-BoldItalic.ttf", bytes: file_2 }, { path: "_roc-docs/fonts/PlusJakartaSans-Italic.ttf", bytes: file_3 }, { path: "_roc-docs/fonts/PlusJakartaSans-Regular.ttf", bytes: file_4 }, { path: "_roc-docs/fonts/SPACE-GROTESK-OFL.txt", bytes: file_5 }, { path: "_roc-docs/fonts/SpaceGrotesk-Bold.ttf", bytes: file_6 }, { path: "_roc-docs/fonts/SpaceGrotesk-Regular.ttf", bytes: file_7 }]
}
