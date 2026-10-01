/* SaveFixLauncher.c — Sakura 存档重定向启动器
 *
 * 做法: CreateProcess(Sakura.exe, SUSPENDED) -> 远线程 LoadLibraryW(savefix.dll)
 *   -> 确认注入成功 -> ResumeThread. 游戏目录即工作目录.
 * 本文件 + savefix.dll 与 Sakura.exe 放同一目录, 双击本启动器开游戏.
 * 任何失败都弹框说明步骤 (绝不静默死), 并终止已建进程.
 *
 * 构建: 见 build.bat (MSVC x86).
 */
#define WIN32_LEAN_AND_MEAN
#define _WIN32_WINNT 0x0601
#include <windows.h>

static void die(const WCHAR *step, DWORD err) {
    WCHAR msg[512];
    wsprintfW(msg, L"%s失败 (err=%lu)\n游戏未启动, 原文件无任何改动。",
        step, err);
    MessageBoxW(NULL, msg, L"启动器 (savefix)", MB_OK | MB_ICONERROR);
}

static int join(WCHAR *dst, const WCHAR *dir, const WCHAR *file) {
    if (lstrlenW(dir) + lstrlenW(file) + 2 >= MAX_PATH) return 0;
    lstrcpyW(dst, dir);
    lstrcatW(dst, L"\\");
    lstrcatW(dst, file);
    return 1;
}

int WINAPI wWinMain(HINSTANCE hi, HINSTANCE hp, LPWSTR cmd, int show) {
    WCHAR selfdir[MAX_PATH], exepath[MAX_PATH], dllpath[MAX_PATH];
    WCHAR *p;
    DWORD n;
    STARTUPINFOW si;
    PROCESS_INFORMATION pi;
    LPVOID remote;
    SIZE_T dllbytes;
    HMODULE k32;
    LPTHREAD_START_ROUTINE fnLoad;
    HANDLE ht;
    DWORD wait, code;
    SIZE_T wrote;
    (void)hi; (void)hp; (void)cmd; (void)show;

    n = GetModuleFileNameW(NULL, selfdir, MAX_PATH);
    if (n == 0 || n >= MAX_PATH) { die(L"定位自身目录", GetLastError()); return 1; }
    p = selfdir + n;
    while (p > selfdir && *(p - 1) != L'\\' && *(p - 1) != L'/') p--;
    *p = L'\0';
    if (!join(exepath, selfdir, L"Sakura.exe") ||
        !join(dllpath, selfdir, L"savefix.dll")) {
        die(L"路径拼接", ERROR_BUFFER_OVERFLOW); return 1;
    }
    if (GetFileAttributesW(exepath) == INVALID_FILE_ATTRIBUTES) {
        die(L"找不到同目录的 Sakura.exe (启动器须与游戏放一起)", ERROR_FILE_NOT_FOUND);
        return 1;
    }
    if (GetFileAttributesW(dllpath) == INVALID_FILE_ATTRIBUTES) {
        die(L"找不到同目录的 savefix.dll", ERROR_FILE_NOT_FOUND);
        return 1;
    }

    ZeroMemory(&si, sizeof(si));
    si.cb = sizeof(si);
    ZeroMemory(&pi, sizeof(pi));
    if (!CreateProcessW(exepath, NULL, NULL, NULL, FALSE,
            CREATE_SUSPENDED, NULL, selfdir, &si, &pi)) {
        die(L"创建游戏进程", GetLastError());
        return 1;
    }

    dllbytes = (lstrlenW(dllpath) + 1) * sizeof(WCHAR);
    remote = VirtualAllocEx(pi.hProcess, NULL, dllbytes,
        MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    if (remote == NULL) {
        die(L"远端内存分配", GetLastError());
        TerminateProcess(pi.hProcess, 1);
        goto cleanup;
    }
    if (!WriteProcessMemory(pi.hProcess, remote, dllpath, dllbytes, &wrote) ||
        wrote != dllbytes) {
        die(L"写入 DLL 路径", GetLastError());
        TerminateProcess(pi.hProcess, 1);
        goto cleanup;
    }
    k32 = GetModuleHandleW(L"kernel32.dll");
    fnLoad = (LPTHREAD_START_ROUTINE)GetProcAddress(k32, "LoadLibraryW");
    if (fnLoad == NULL) {
        die(L"定位 LoadLibraryW", GetLastError());
        TerminateProcess(pi.hProcess, 1);
        goto cleanup;
    }
    ht = CreateRemoteThread(pi.hProcess, NULL, 0, fnLoad, remote, 0, NULL);
    if (ht == NULL) {
        die(L"创建远端线程", GetLastError());
        TerminateProcess(pi.hProcess, 1);
        goto cleanup;
    }
    wait = WaitForSingleObject(ht, 30000);
    if (wait != WAIT_OBJECT_0 || !GetExitCodeThread(ht, &code) || code == 0) {
        die(L"注入 savefix.dll (远端 LoadLibrary)",
            wait != WAIT_OBJECT_0 ? WAIT_TIMEOUT : GetLastError());
        TerminateThread(ht, 1);
        TerminateProcess(pi.hProcess, 1);
        CloseHandle(ht);
        goto cleanup;
    }
    CloseHandle(ht);
    VirtualFreeEx(pi.hProcess, remote, 0, MEM_RELEASE);
    ResumeThread(pi.hThread);
    CloseHandle(pi.hThread);
    CloseHandle(pi.hProcess);
    return 0;

cleanup:
    CloseHandle(pi.hThread);
    CloseHandle(pi.hProcess);
    return 1;
}
