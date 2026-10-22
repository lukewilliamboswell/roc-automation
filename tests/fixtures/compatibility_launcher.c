/* Native Windows fixture launcher. No shell interprets the test arguments.
 * basic-cli's JobObject owns this process and its Python child. */
#include <windows.h>
#include <process.h>
#include <stdlib.h>
#include <wchar.h>

/* The CRT spawn API concatenates argv without quoting. Preserve whitespace,
 * quotes and trailing backslashes with Microsoft's command-line quoting rules. */
static wchar_t *quote(const wchar_t *value) {
    wchar_t *result = calloc(wcslen(value) * 2 + 3, sizeof(wchar_t));
    if (!result) return NULL;
    wchar_t *out = result;
    *out++ = L'"';
    size_t slashes = 0;
    for (const wchar_t *in = value; ; ++in) {
        if (*in == L'\\') { ++slashes; continue; }
        size_t count = (*in == L'"' || !*in) ? slashes * 2 : slashes;
        while (count--) *out++ = L'\\';
        slashes = 0;
        if (*in == L'"') *out++ = L'\\';
        if (!*in) break;
        *out++ = *in;
    }
    *out++ = L'"';
    *out = 0;
    return result;
}

int wmain(int argc, wchar_t **argv) {
    wchar_t script[32768];
    if (!GetModuleFileNameW(NULL, script, 32768)) return 120;
    wchar_t *extension = wcsrchr(script, L'.');
    if (!extension) return 121;
    wcscpy(extension, L".py");
    wchar_t *python = _wgetenv(L"FAKE_PYTHON");
    if (!python) return 122;
    wchar_t **arguments = calloc((size_t)argc + 2, sizeof(wchar_t *));
    if (!arguments) return 123;
    arguments[0] = quote(python);
    arguments[1] = quote(script);
    for (int i = 1; i < argc; ++i) arguments[i + 1] = quote(argv[i]);
    for (int i = 0; i <= argc; ++i) if (!arguments[i]) return 123;
    intptr_t result = _wspawnv(_P_WAIT, python, (const wchar_t *const *)arguments);
    for (int i = 0; i <= argc; ++i) free(arguments[i]);
    free(arguments);
    return result == -1 ? 124 : (int)result;
}
