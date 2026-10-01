/* savefix.c — FVP 引擎存档目录重定向 (Sakura 樱花萌放)
 *
 * 背景: Sakura.exe 内唯一 SHGetFolderPathA(NULL,5,...) 调用点 (0x4427cd,
 * IAT 槽, csidl=5=My Documents), 返回 ANSI 路径; 中文用户名在转区下变 '?'
 * (非法文件名字符) 或乱码, 存档读写崩. HCB 脚本零路径逻辑, 只能动这一层.
 *
 * 做法: IAT hook (改本进程内导入表一项, 原 exe 零字节修改).
 *   仅当 csidl==CSIDL_PERSONAL(5) 时返回 <exe_dir>\userdata\ ,
 *   其余原样透传. 后缀 FAVORITE\<标题>\save\ 与文件名模板走原引擎代码.
 *   首次拦截时做一次性迁移 (老位置有档→全量复制, 永不删源).
 * 约束: 不动注册表, 不改 exe, 删 DLL 即回滚.
 * 安全: DllMain 内只做 IAT 换表 (无 LoadLibrary/无 UI/无文件 IO);
 *   目录创建与迁移全部延迟到首次拦截调用 (引擎线程, 无 loader-lock 顾虑).
 *
 * 构建: 见 build.bat (MSVC x86, /MT 零依赖).
 */
#define WIN32_LEAN_AND_MEAN
#define _WIN32_WINNT 0x0601
#include <windows.h>

#define CSIDL_PERSONAL 0x0005

typedef HRESULT (STDAPICALLTYPE *PFN_SHGetFolderPathA)(
    HWND hwndOwner, int nFolder, HANDLE hToken, DWORD dwFlags, LPSTR pszPath);

static PFN_SHGetFolderPathA s_orig = NULL;
static volatile LONG s_state = 0; /* 0=未初始化 1=初始化中 2=就绪 3=放弃(透传) */
static volatile LONG s_once = 0;  /* 首次拦截初始化标记 */
static WCHAR s_baseW[MAX_PATH];   /* <exe_dir>\userdata\ (Unicode 真值) */
static char  s_baseA[MAX_PATH];   /* ANSI 形态 (给引擎) */
static int   s_base_ok = 0;
static int   s_warned = 0;

static void warn_once(const WCHAR *msg) {
    if (s_warned) return;
    s_warned = 1;
    MessageBoxW(NULL, msg, L"存档重定向 (savefix)",
        MB_OK | MB_ICONWARNING);
}

/* exe 名必须是 Sakura.exe 才挂钩 (防误伤其他宿主) */
static int host_is_sakura(void) {
    WCHAR path[MAX_PATH];
    WCHAR *p;
    DWORD n;
    n = GetModuleFileNameW(NULL, path, MAX_PATH);
    if (n == 0 || n >= MAX_PATH) return 0;
    p = path + n;
    while (p > path && *(p - 1) != L'\\' && *(p - 1) != L'/') p--;
    return lstrcmpiW(p, L"Sakura.exe") == 0;
}

/* 计算重定向基址 (静默, 无 UI; DllMain 内调用安全).
 * 成功返回 1 (s_baseA 有效), 失败返回 0 (之后透传 + 首次警告). */
static int compute_base(void) {
    WCHAR exe[MAX_PATH], *p;
    DWORD n;
    BOOL used = FALSE;
    int len;
    n = GetModuleFileNameW(NULL, exe, MAX_PATH);
    if (n == 0 || n >= MAX_PATH - 32) return 0;
    p = exe + n;
    while (p > exe && *(p - 1) != L'\\' && *(p - 1) != L'/') p--;
    *p = L'\0';
    lstrcpyW(s_baseW, exe);
    if (lstrlenW(s_baseW) + 10 >= MAX_PATH) return 0;
    lstrcatW(s_baseW, L"userdata\\");
    /* ANSI 可表示性校验: 含不可表示字符则拒绝重定向 (防更惨的静默崩) */
    len = WideCharToMultiByte(CP_ACP, WC_NO_BEST_FIT_CHARS,
        s_baseW, -1, NULL, 0, NULL, &used);
    if (len <= 0 || len >= MAX_PATH || used) return 0;
    WideCharToMultiByte(CP_ACP, 0, s_baseW, -1, s_baseA, MAX_PATH, NULL, NULL);
    return 1;
}

/* 路径拼接 (带长度守卫), 成功返回 1 */
static int join3(WCHAR *dst, const WCHAR *a, const WCHAR *b, const WCHAR *c) {
    if (lstrlenW(a) + lstrlenW(b) + lstrlenW(c) + 1 >= MAX_PATH) return 0;
    lstrcpyW(dst, a);
    lstrcatW(dst, b);
    lstrcatW(dst, c);
    return 1;
}

/* 递归复制 srcDir -> dstDir (Unicode). 遇存在文件跳过 (永不覆盖). */
static void copy_tree_w(const WCHAR *src, const WCHAR *dst) {
    WCHAR find[MAX_PATH], s[MAX_PATH], t[MAX_PATH];
    WIN32_FIND_DATAW fd;
    HANDLE h;
    CreateDirectoryW(dst, NULL);
    if (!join3(find, src, L"\\*", L"")) return;
    h = FindFirstFileW(find, &fd);
    if (h == INVALID_HANDLE_VALUE) return;
    do {
        if (lstrcmpW(fd.cFileName, L".") == 0 ||
            lstrcmpW(fd.cFileName, L"..") == 0) continue;
        if (!join3(s, src, L"\\", fd.cFileName)) continue;
        if (!join3(t, dst, L"\\", fd.cFileName)) continue;
        if (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) {
            copy_tree_w(s, t);
        } else {
            CopyFileW(s, t, TRUE); /* 目标存在则跳过 */
        }
    } while (FindNextFileW(h, &fd));
    FindClose(h);
}

static int marker_exists(void) {
    WCHAR m[MAX_PATH];
    DWORD a;
    if (!join3(m, s_baseW, L"FAVORITE\\.migrated", L"")) return 1; /* 守卫失败当已迁 */
    a = GetFileAttributesW(m);
    return (a != INVALID_FILE_ATTRIBUTES);
}

static void touch_marker(void) {
    WCHAR m[MAX_PATH];
    HANDLE h;
    if (!join3(m, s_baseW, L"FAVORITE\\.migrated", L"")) return;
    h = CreateFileW(m, GENERIC_WRITE, 0, NULL, CREATE_NEW,
        FILE_ATTRIBUTE_NORMAL, NULL);
    if (h != INVALID_HANDLE_VALUE) CloseHandle(h);
}

/* 一次性迁移: 新位置无 FAVORITE 且老位置有 -> 全量复制. (引擎线程调用) */
static void maybe_migrate(void) {
    WCHAR legacy[MAX_PATH], newfav[MAX_PATH];
    char ansibuf[MAX_PATH];
    DWORD la, na;
    HRESULT hr;
    if (marker_exists()) return;
    if (s_orig == NULL) return;
    hr = s_orig(NULL, CSIDL_PERSONAL, NULL, 0, ansibuf);
    if (hr != S_OK) return;
    if (MultiByteToWideChar(CP_ACP, 0, ansibuf, -1,
            legacy, MAX_PATH) <= 0) return;
    if (!join3(legacy, legacy, L"\\FAVORITE", L"")) return;
    la = GetFileAttributesW(legacy);
    if (la == INVALID_FILE_ATTRIBUTES ||
        !(la & FILE_ATTRIBUTE_DIRECTORY)) return; /* 老位置无档: 无事可做 */
    if (!join3(newfav, s_baseW, L"FAVORITE", L"")) return;
    na = GetFileAttributesW(newfav);
    if (na != INVALID_FILE_ATTRIBUTES &&
        (na & FILE_ATTRIBUTE_DIRECTORY)) {
        touch_marker();
        return; /* 新位置已有: 不覆盖 */
    }
    copy_tree_w(legacy, newfav);
    touch_marker();
}

/* 首次拦截初始化 (目录预建 + 迁移; 在引擎线程, 非 loader-lock) */
static void first_time_init(void) {
    if (InterlockedCompareExchange(&s_once, 1, 0) != 0) return;
    CreateDirectoryW(s_baseW, NULL);
    maybe_migrate();
}

static HRESULT STDAPICALLTYPE Hook_SHGetFolderPathA(
    HWND hwndOwner, int nFolder, HANDLE hToken, DWORD dwFlags, LPSTR pszPath) {
    if (s_orig == NULL) return E_FAIL;
    if (nFolder != CSIDL_PERSONAL) {
        return s_orig(hwndOwner, nFolder, hToken, dwFlags, pszPath);
    }
    if (s_state != 2) {
        return s_orig(hwndOwner, nFolder, hToken, dwFlags, pszPath);
    }
    if (!s_base_ok) {
        warn_once(L"游戏安装路径含当前 ANSI 代码页无法表示的字符,"
                  L"存档重定向已自动关闭(保持原行为)。\n"
                  L"请将游戏安装到纯英文目录后重试。");
        return s_orig(hwndOwner, nFolder, hToken, dwFlags, pszPath);
    }
    if (pszPath == NULL) return E_INVALIDARG;
    first_time_init();
    lstrcpyA(pszPath, s_baseA);
    return S_OK;
}

/* 在本进程导入表中按名定位 SHELL32!SHGetFolderPathA 的 IAT 槽并替换. */
static int install_hook(void) {
    BYTE *base;
    IMAGE_DOS_HEADER *dos;
    IMAGE_NT_HEADERS32 *nt;
    IMAGE_IMPORT_DESCRIPTOR *desc;
    DWORD *slot;
    char *dllname;
    int i;
    DWORD old;
    IMAGE_THUNK_DATA32 *orig, *iat;
    IMAGE_IMPORT_BY_NAME *by;
    base = (BYTE *)GetModuleHandleW(NULL);
    if (base == NULL) return 0;
    dos = (IMAGE_DOS_HEADER *)base;
    if (dos->e_magic != IMAGE_DOS_SIGNATURE) return 0;
    nt = (IMAGE_NT_HEADERS32 *)(base + dos->e_lfanew);
    if (nt->Signature != IMAGE_NT_SIGNATURE) return 0;
    desc = (IMAGE_IMPORT_DESCRIPTOR *)(base +
        nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_IMPORT].VirtualAddress);
    for (; desc->Name != 0; desc++) {
        dllname = (char *)(base + desc->Name);
        if (lstrcmpiA(dllname, "SHELL32.dll") != 0) continue;
        if (desc->OriginalFirstThunk == 0 || desc->FirstThunk == 0) return 0;
        orig = (IMAGE_THUNK_DATA32 *)(base + desc->OriginalFirstThunk);
        iat = (IMAGE_THUNK_DATA32 *)(base + desc->FirstThunk);
        for (i = 0; orig[i].u1.AddressOfData != 0; i++) {
            if (orig[i].u1.Ordinal & IMAGE_ORDINAL_FLAG32) continue;
            by = (IMAGE_IMPORT_BY_NAME *)(base + orig[i].u1.AddressOfData);
            if (strcmp((const char *)by->Name, "SHGetFolderPathA") == 0) {
                slot = (DWORD *)&iat[i];
                s_orig = (PFN_SHGetFolderPathA)(*slot);
                if (s_orig == NULL) return 0;
                if (!VirtualProtect(slot, sizeof(DWORD),
                        PAGE_READWRITE, &old)) return 0;
                *slot = (DWORD)(Hook_SHGetFolderPathA);
                VirtualProtect(slot, sizeof(DWORD), old, &old);
                return 1;
            }
        }
        return 0;
    }
    return 0;
}

BOOL WINAPI DllMain(HINSTANCE hinst, DWORD reason, LPVOID rsv) {
    (void)hinst; (void)rsv;
    if (reason == DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(hinst);
        if (!host_is_sakura()) return TRUE; /* 非目标宿主: 静默不挂钩 */
        if (InterlockedCompareExchange(&s_state, 1, 0) != 0) return TRUE;
        if (!install_hook()) { s_state = 3; return TRUE; }
        s_base_ok = compute_base(); /* 静默; 失败则 Hook 内警告+透传 */
        s_state = 2;
    }
    return TRUE;
}
