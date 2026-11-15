/* JivDesktop.exe: tiny launcher for the installed layout
     {app}\JivDesktop.exe   {app}\python\pythonw.exe   {app}\app\run.py
   Runs pythonw.exe app\run.py <args> with no console window, waits for it and returns its exit code
   (the update watchdog relies on that). Sets JIV_LAUNCHER so Jiv can restart itself through this exe. */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <wchar.h>

static const wchar_t *skip_argv0(const wchar_t *c) {
    if (*c == L'"') { c++; while (*c && *c != L'"') c++; if (*c) c++; }
    else while (*c && *c != L' ' && *c != L'\t') c++;
    while (*c == L' ' || *c == L'\t') c++;
    return c;
}

int WINAPI wWinMain(HINSTANCE h, HINSTANCE p, PWSTR cmd, int show) {
    wchar_t exe[MAX_PATH], dir[MAX_PATH], py[MAX_PATH], script[MAX_PATH];
    DWORD n = GetModuleFileNameW(NULL, exe, MAX_PATH);
    if (!n || n >= MAX_PATH) return 2;
    wcscpy(dir, exe);
    wchar_t *slash = wcsrchr(dir, L'\\'); if (slash) *slash = 0;
    _snwprintf(py, MAX_PATH, L"%ls\\python\\pythonw.exe", dir);
    _snwprintf(script, MAX_PATH, L"%ls\\app\\run.py", dir);
    if (GetFileAttributesW(py) == INVALID_FILE_ATTRIBUTES || GetFileAttributesW(script) == INVALID_FILE_ATTRIBUTES) {
        MessageBoxW(NULL, L"Jiv Desktop is missing files (python\\pythonw.exe or app\\run.py).\nPlease reinstall it with JivSetup.exe.",
                    L"Jiv Desktop", MB_ICONERROR); return 3;
    }
    const wchar_t *args = skip_argv0(GetCommandLineW());
    size_t len = wcslen(py) + wcslen(script) + wcslen(args) + 16;
    wchar_t *line = (wchar_t *)HeapAlloc(GetProcessHeap(), 0, len * sizeof(wchar_t));
    _snwprintf(line, len, L"\"%ls\" \"%ls\" %ls", py, script, args);
    SetEnvironmentVariableW(L"JIV_LAUNCHER", exe);
    SetEnvironmentVariableW(L"JIV_INSTALLED", L"1");
    SetEnvironmentVariableW(L"PYTHONDONTWRITEBYTECODE", NULL);
    STARTUPINFOW si; PROCESS_INFORMATION pi; ZeroMemory(&si, sizeof si); si.cb = sizeof si;
    wchar_t appdir[MAX_PATH]; _snwprintf(appdir, MAX_PATH, L"%ls\\app", dir);
    if (!CreateProcessW(py, line, NULL, NULL, FALSE, CREATE_NO_WINDOW, NULL, appdir, &si, &pi)) {
        MessageBoxW(NULL, L"Could not start Jiv Desktop (pythonw.exe failed to launch).", L"Jiv Desktop", MB_ICONERROR); return 4;
    }
    WaitForSingleObject(pi.hProcess, INFINITE);
    DWORD code = 0; GetExitCodeProcess(pi.hProcess, &code);
    CloseHandle(pi.hThread); CloseHandle(pi.hProcess);
    return (int)code;
}
