#!/usr/bin/env python3
"""编辑器微服务（标准库 only）：静态页 + 场景/回放/贴图 + 一键写回。

用法：
  python3 serve_editor.py --sample-dir /tmp/fvp-sample [--hcb 原.hcb]
      [--addrmap traced.json] [--out-hcb 新.hcb] [--port 8000]
  浏览器开 http://localhost:8000 →「载入示例工程」。

路径也可在网页「工程配置」区填写（服务端本地路径）：即时生效并存
server_config.json（不进仓），下次启动自动恢复；命令行显式参数优先。

写回安全：只调 patch_hcb.py（等宽/操作码/旧值三校验），默认绝不覆盖原文件
（必须显式 --out-hcb 或配置区另指输出）；写回后可经 /api/download?kind=hcb
下载到浏览器本地。
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(HERE, "web")
REPO = HERE
ARGS = None

# 可配路径（网页 /api/config 热切换 + server_config.json 落盘；命令行显式参数优先）。
CONFIG_KEYS = ("sample_dir", "simple_dir", "script", "hcb", "addrmap",
               "out_hcb", "fvp_base")
CONFIG_FILE = os.path.join(HERE, "server_config.json")
CONFIG = {}


def C(key):
    return CONFIG.get(key, "")


def config_check(key, val):
    """单项校验：返回 (ok, msg)。空串=未配置（sample_dir 除外，必须有效）。"""
    if not val:
        if key == "sample_dir":
            return False, "sample_dir 不能为空"
        return True, "未配置"
    if key in ("sample_dir", "simple_dir", "fvp_base"):
        if not os.path.isdir(val):
            return False, f"目录不存在: {val}"
        return True, "目录就绪"
    if key == "out_hcb":
        # 与 hcb 的互斥由 POST 做最终态复核（hcb 可能同包变更）。
        parent = os.path.dirname(os.path.abspath(val))
        if not os.path.isdir(parent):
            return False, f"父目录不存在: {parent}"
        return True, "可写入" if not os.path.isfile(val) else "文件已存在（将复用为输出）"
    # script / hcb / addrmap：必须是已存在文件
    if not os.path.isfile(val):
        return False, f"文件不存在: {val}"
    ext = {"script": ".txt", "hcb": ".hcb", "addrmap": ".json"}.get(key)
    if ext and not val.lower().endswith(ext):
        return False, f"{key} 应为 {ext} 文件"
    return True, "文件就绪"


def load_config(args):
    """命令行显式 > server_config.json > 默认；fvp_base 同步进 os.environ。"""
    try:
        saved = json.load(open(CONFIG_FILE, encoding="utf-8"))
        if not isinstance(saved, dict):
            saved = {}
    except Exception:
        saved = {}

    def pick(key, cli_val, default=""):
        # 命令行未传（None/空）才走配置文件。
        if cli_val:
            return cli_val
        v = saved.get(key, "")
        return v if v else default

    CONFIG.update({
        "sample_dir": pick("sample_dir", args.sample_dir, "/tmp/fvp-sample"),
        "simple_dir": pick("simple_dir", args.simple_dir),
        "script": pick("script", args.script),
        "hcb": pick("hcb", args.hcb),
        "addrmap": pick("addrmap", args.addrmap),
        "out_hcb": pick("out_hcb", args.out_hcb),
        # 环境变量视为显式配置（start_editor.sh/bat 照常用）：env > 文件
        "fvp_base": args.fvp_base or os.environ.get("FVP_BASE_PATH", "")
        or saved.get("fvp_base", ""),
    })
    if C("fvp_base"):
        os.environ["FVP_BASE_PATH"] = C("fvp_base")


def save_config():
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump({k: C(k) for k in CONFIG_KEYS}, f,
                      ensure_ascii=False, indent=1)
    except Exception:
        pass


def reset_instant():
    INSTANT["text_hash"] = None
    INSTANT["linemap"] = None
    INSTANT["entry"] = None

# P2: rebuild 后台任务表 {job: {state, log, t0}}
JOBS = {}
JOBS_LOCK = threading.Lock()
JOB_SEQ = [0]

# P3: 即时预览状态（最新优先：新请求杀掉旧 vm 进程）
INSTANT = {"lock": threading.Lock(), "proc": None, "text_hash": None,
           "entry": None, "linemap": None, "line_tick": {}}
# P3: 构建锁（即时 build_simple 与全量 preview_simple.sh 都写 .test.chb）
BUILD_LOCK = threading.Lock()


def _sha1_file(path):
    import hashlib
    h = hashlib.sha1()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()


def build_simple(simple_dir, script_txt):
    """P3: hcb_build 编译 + 反推剧本入口 + 读 linemap。返回 (entry, linemap)。
    文本未变且产物齐则跳过编译（秒级跟手的关键）。"""
    import hashlib
    with open(script_txt, "rb") as f:
        digest = hashlib.sha1(f.read()).hexdigest()
    chb = os.path.join(simple_dir, ".test.chb")
    lmp = os.path.join(simple_dir, ".linemap.json")
    if (INSTANT["text_hash"] == digest and INSTANT["linemap"]
            and os.path.isfile(chb) and os.path.isfile(lmp)):
        return INSTANT["entry"], INSTANT["linemap"]
    os.makedirs(os.path.join(simple_dir, "base"), exist_ok=True)
    for name in ("base.chb", "cg_loaded.txt"):
        dst = os.path.join(simple_dir, "base", name)
        if not os.path.isfile(dst):
            shutil.copy(os.path.join(simple_dir, name), dst)
    import shutil as _sh
    with BUILD_LOCK:
        # script 默认就是 <simple>/base/Script.txt：同文件跳过（否则 SameFileError）。
        # normcase 照顾 Windows 大小写不敏感。
        dst_txt = os.path.join(simple_dir, "base", "Script.txt")
        same = os.path.normcase(os.path.abspath(script_txt)) == \
            os.path.normcase(os.path.abspath(dst_txt))
        if not same:
            _sh.copy(script_txt, dst_txt)
        blog = os.path.join(simple_dir, "base", "build.log")
        p = subprocess.run([sys.executable, "hcb_build.py"], cwd=simple_dir,
                           stdout=open(blog, "w"), stderr=subprocess.STDOUT, timeout=120)
    if p.returncode != 0 or not os.path.isfile(chb):
        line = None
        try:
            nums = re.findall(r"^(\d+)\s*$",
                              open(blog, encoding="utf-8", errors="replace").read(), re.M)
            if nums:
                line = int(nums[-1]) + 1
        except Exception:
            pass
        tail = open(blog, encoding="utf-8", errors="replace").read().splitlines()[-6:]
        raise RuntimeError(("BUILD_FAIL_TXT_LINE=%d\n" % line if line else "")
                           + "\n".join(tail))
    lm = json.load(open(lmp, encoding="utf-8"))
    # linemap 自带 new_off（编译器直写）；diff 反推只当后备（new_off==base_off
    # 时 oribytes 无 patch，diff 无候选，如全预注册 CG 的剧本）。
    try:
        entry = int(lm.get("new_off", 0)) or None
    except Exception:
        entry = None
    if not entry:
        entry = _derive_entry(os.path.join(simple_dir, "base", "base.chb"), chb)
    INSTANT["text_hash"] = digest
    INSTANT["entry"] = entry
    INSTANT["linemap"] = lm
    # 全量回放的行→tick 提示（有则断点 ticks 更准）
    INSTANT["line_tick"] = {}
    try:
        rj = json.load(open(os.path.join(C("sample_dir"), "replay.json"), encoding="utf-8"))
        for r in rj.get("rows", []):
            if r.get("line") is not None:
                INSTANT["line_tick"][r["line"]] = max(
                    INSTANT["line_tick"].get(r["line"], 0), r.get("tick", 0))
    except Exception:
        pass
    return entry, lm


def _derive_entry(base_chb, built_chb):
    """preview_simple.sh 步骤2 的 python 版：diff 反推 new_off。"""
    import struct
    a = open(base_chb, "rb").read()
    b = open(built_chb, "rb").read()
    base_off = 0x0008AEC7
    entry = struct.unpack_from("<I", b, 0)[0]
    cands = set()
    i, n = 4, min(len(a), base_off)
    while i < n:
        if a[i] != b[i]:
            j = i
            while j < n and a[j] != b[j]:
                j += 1
            for k in range(max(4, i - 3), j):
                v = struct.unpack_from("<I", b, k)[0]
                if base_off < v < entry:
                    cands.add(v)
            i = j
        else:
            i += 1
    if not cands:
        raise RuntimeError("推不出 new_off")
    return max(cands)


def line_target(lm, L):
    """P3: 光标行 → 断点 pc（该行字节尾；空行取之前最近有效行的尾）。"""
    base = int(lm.get("base_off", 0))
    best = None
    for e in lm.get("lines", []):
        if e["line"] <= L and base + int(e["end"]) > base + int(e["start"]):
            best = base + int(e["end"])
    if best is None:
        best = int(lm.get("new_off", base))
    return best


def run_instant(simple_dir, script_txt, L):
    """P3: 编译（如需）+ vm break-pc 跑到行尾 → 快照。返回 dict。"""
    import re as _re, time as _t
    t0 = _t.time()
    # FVP_VM_BIN: release 二进制（debug 约 40t/s，长剧本分钟级；release 秒级）。
    # 默认顺序：环境显式 > ext4 缓存 release > 包内 bin > 缓存 debug。
    vm = os.environ.get("FVP_VM_BIN", "")
    if not vm:
        cands = [
            os.path.expanduser("~/.cache/cargo-target/fvp-preview/release/fvp-preview-vm"),
            os.path.join(REPO, "bin", "fvp-preview-vm"),
            os.path.expanduser("~/.cache/cargo-target/fvp-preview/debug/fvp-preview-vm"),
        ]
        vm = next((c for c in cands if os.path.isfile(c) and os.access(c, os.X_OK)),
                  cands[-1])
    entry, lm = build_simple(simple_dir, script_txt)
    target = line_target(lm, L)
    hint = INSTANT["line_tick"].get(L)
    cap = (hint + 150) if hint else 2000
    idir = C("sample_dir").rstrip("/") + ".instant"
    os.makedirs(os.path.join(idir, "tex"), exist_ok=True)
    # 拷一份 chb 再跑：全量重建会重写 .test.chb，直接读会撕裂。
    chb_run = os.path.join(idir, "instant.chb")
    with BUILD_LOCK:
        import shutil as _sh2
        _sh2.copy(os.path.join(simple_dir, ".test.chb"), chb_run)
    fbh = C("fvp_base") or os.environ.get("FVP_BASE_PATH", "")
    if not fbh or not os.path.isdir(fbh):
        return {"ok": False, "error": "服务端缺 FVP_BASE_PATH（正式版 moyu 目录）"}
    cmd = [vm, chb_run,
           "--ticks", str(cap), "--entry-pc", str(entry),
           "--auto-click", "30", "--nls", "gbk",
           "--png-dir", os.path.join(idir, "tex"),
           "--break-pc", str(target)]
    env = dict(os.environ)
    with INSTANT["lock"]:
        old = INSTANT["proc"]
        if old and old.poll() is None:
            try:
                old.kill()
            except Exception:
                pass
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, env=env)
        INSTANT["proc"] = p
    try:
        out, err = p.communicate(timeout=240)
    except subprocess.TimeoutExpired:
        p.kill()
        return {"ok": False, "error": f"即时预览超时（{cap} ticks 未跑完，行可能在分支外）"}
    with INSTANT["lock"]:
        if INSTANT["proc"] is p:
            INSTANT["proc"] = None
    if p.returncode is not None and p.returncode < 0:
        # 被更新的请求 kill（或 vm 崩溃）；前端静默丢弃，等新请求的回包。
        return {"ok": False, "error": f"superseded(rc={p.returncode})"}
    ticks_used = len(_re.findall(r"tickend \d+", err or ""))
    m = _re.search(r"\[vm\] 停止：(.*)", err or "")
    why = m.group(1).strip() if m else ""
    hit = ("break-pc" in why) or ("越过" in why)
    try:
        snap = json.loads(out)
    except Exception as e:
        return {"ok": False, "error": f"快照解析失败: {e}\n{(err or '')[-800:]}"}
    ms = int((_t.time() - t0) * 1000)
    return {"ok": True, "snapshot": snap, "line": L, "pc": target,
            "ticks": ticks_used, "ms": ms, "hit": hit, "why": why}


def jsend(h, obj, code=200):
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    h.send_response(code)
    h.send_header("Content-Type", "application/json; charset=utf-8")
    h.send_header("Content-Length", str(len(body)))
    h.end_headers()
    h.wfile.write(body)


def script_path():
    if C("script"):
        return C("script")
    if C("simple_dir"):
        return os.path.join(C("simple_dir"), "base", "Script.txt")
    return ""


def run_rebuild(job, simple_dir, script_txt, sample_dir, ticks, port):
    """P2: 后台跑 preview_simple.sh（SKIP_SERVE=1）到 sample.next，成功则换位。"""
    def log(s):
        with JOBS_LOCK:
            JOBS[job]["log"] += s + "\n"
    try:
        nxt = sample_dir.rstrip("/") + ".next"
        if os.path.isdir(nxt):
            shutil.rmtree(nxt)
        env = dict(os.environ)
        env["SKIP_SERVE"] = "1"
        env["TICKS"] = str(ticks)
        cmd = ["bash", os.path.join(REPO, "preview_simple.sh"),
               simple_dir, script_txt, nxt, str(port)]
        log("$ " + " ".join(cmd))
        with BUILD_LOCK:
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 text=True, bufsize=1, env=env, cwd=simple_dir)
            for line in p.stdout:
                with JOBS_LOCK:
                    JOBS[job]["log"] += line
            rc = p.wait()
        if rc != 0:
            tail = JOBS[job]["log"][-2000:]
            with JOBS_LOCK:
                JOBS[job]["state"] = "error"
                JOBS[job]["error"] = f"管线退出码 {rc}\n" + tail
            return
        # 换位：next -> sample（旧的挪 .bak，读一半的 torn JSON 不会暴露太久；
        # 前端在 state=done 后才重拉）。
        bak = sample_dir.rstrip("/") + ".bak"
        if os.path.isdir(bak):
            shutil.rmtree(bak)
        if os.path.isdir(sample_dir):
            os.rename(sample_dir, bak)
        os.rename(nxt, sample_dir)
        rows, diffs = None, None
        try:
            rj = json.load(open(os.path.join(sample_dir, "replay.json"), encoding="utf-8"))
            rows = len(rj.get("rows", []))
        except Exception:
            pass
        for line in JOBS[job]["log"].splitlines()[::-1]:
            if "diffs=" in line:
                diffs = line.strip()
                break
        with JOBS_LOCK:
            JOBS[job]["state"] = "done"
            JOBS[job]["rows"] = rows
            JOBS[job]["diffs"] = diffs
    except Exception as e:
        with JOBS_LOCK:
            JOBS[job]["state"] = "error"
            JOBS[job]["error"] = f"任务异常: {e}"


def resolve_out(hcb):
    """写回输出路径（writeback 与 download 共用同一规则）。"""
    return C("out_hcb") or (hcb + ".edit.hcb")


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _file(self, path, ctype):
        try:
            with open(path, "rb") as f:
                body = f.read()
        except OSError:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        if u.path in ("/", "/index.html"):
            return self._file(os.path.join(WEB, "index.html"), "text/html; charset=utf-8")
        if u.path == "/api/scene":
            return self._file(os.path.join(C("sample_dir"), "scene.json"), "application/json")
        if u.path == "/api/replay":
            return self._file(os.path.join(C("sample_dir"), "replay.json"), "application/json")
        if u.path == "/api/addrmap":
            ap = C("addrmap") or os.path.join(C("sample_dir"), "addrmap.json")
            return self._file(ap, "application/json")
        if u.path == "/api/paths":
            # 面板“来源显示”：所有文件归属都在服务端（浏览器填的路径服务端够不着）。
            sp = script_path()
            hcb = C("hcb") or ""
            am = C("addrmap") or os.path.join(C("sample_dir"), "addrmap.json")
            sc = os.path.join(C("sample_dir"), "scene.json")
            rj = os.path.join(C("sample_dir"), "replay.json")
            return jsend(self, {
                "sample_dir": C("sample_dir"),
                "scene": sc if os.path.isfile(sc) else "",
                "replay": rj if os.path.isfile(rj) else "",
                "addrmap": am if os.path.isfile(am) else "",
                "hcb": hcb, "hcb_ok": bool(hcb and os.path.isfile(hcb)),
                "out_hcb": C("out_hcb") or ((hcb + ".edit.hcb") if hcb else ""),
                "simple_dir": C("simple_dir") or "",
                "script": sp,
                "writeback_ready": bool(hcb and os.path.isfile(hcb)
                                        and os.path.isfile(am)),
            })
        if u.path == "/api/config":
            # 工程配置：当前值 + 逐项存在性 + 写回就绪（前端配置区用）。
            cfg = {k: C(k) for k in CONFIG_KEYS}
            checks = {}
            for k in CONFIG_KEYS:
                ok, msg = config_check(k, cfg[k])
                checks[k] = {"ok": ok, "msg": msg}
            hcb = cfg["hcb"]
            am = cfg["addrmap"] or os.path.join(cfg["sample_dir"], "addrmap.json")
            return jsend(self, {
                "ok": True,
                "config": cfg,
                "checks": checks,
                "writeback_ready": bool(hcb and os.path.isfile(hcb)
                                        and os.path.isfile(am)),
            })
        if u.path == "/api/script":
            # P2: 读剧本 txt（磁盘 GBK → JSON UTF-8）。
            sp = script_path()
            if not sp or not os.path.isfile(sp):
                return jsend(self, {"ok": False, "error": "服务端未配 --script"})
            try:
                with open(sp, "rb") as f:
                    text = f.read().decode("gbk")
            except Exception as e:
                return jsend(self, {"ok": False, "error": f"读剧本失败: {e}"})
            return jsend(self, {"ok": True, "path": sp, "text": text})
        if u.path == "/api/rebuild":
            job = (q.get("job") or [""])[0]
            with JOBS_LOCK:
                j = JOBS.get(job)
                if not j:
                    return jsend(self, {"ok": False, "error": "任务不存在"})
                out = {"ok": True, "state": j["state"], "log": j["log"][-3000:]}
                if j["state"] == "done":
                    out.update({"rows": j.get("rows"), "diffs": j.get("diffs")})
                if j["state"] == "error":
                    out.update({"error": j.get("error")})
            return jsend(self, out)
        if u.path == "/api/tex":
            p = (q.get("path") or [""])[0]
            # replay 里是相对 sample_dir 的路径（tex/xxx.png）；相对路径一律
            # 以 sample_dir 为根解析（以前直接 isfile，cwd 不对就全 404）。
            if p and not os.path.isabs(p):
                p = os.path.join(C("sample_dir"), p)
            p = os.path.normpath(p)
            # 允许 sample_dir 本体 + 即时预览用的 <sample>.instant 兄弟目录
            #（instant 快照里是 --png-dir 落盘的绝对路径）。
            roots = [os.path.normpath(C("sample_dir")),
                     os.path.normpath(C("sample_dir").rstrip("/") + ".instant")]
            if not any(p == r or p.startswith(r + os.sep) for r in roots):
                return jsend(self, {"ok": False, "error": "非法路径"}, 404)
            if not p or not os.path.isfile(p):
                # 不用 send_error(中文)：BaseHTTPRequestHandler 按 latin-1 编码
                # message，中文直接炸 handler 线程。
                return jsend(self, {"ok": False, "error": "贴图不存在"}, 404)
            ext = os.path.splitext(p)[1].lower()
            ctype = {"png": "image/png", "jpg": "image/jpeg", "bmp": "image/bmp"}.get(
                ext.lstrip("."), "application/octet-stream")
            return self._file(p, ctype)
        if u.path == "/api/download":
            # 导出下载（白名单：只放已配置的写回输出；防任意读盘）。
            kind = (q.get("kind") or [""])[0]
            if kind != "hcb":
                return jsend(self, {"ok": False, "error": "kind 仅支持 hcb"}, 404)
            hcb = C("hcb")
            if not hcb or not os.path.isfile(hcb):
                return jsend(self, {"ok": False, "error": "未配置 hcb，先配好并写回一次"}, 404)
            out = resolve_out(hcb)
            if not os.path.isfile(out):
                return jsend(self, {"ok": False,
                                    "error": f"输出尚不存在，先点写回: {out}"}, 404)
            try:
                with open(out, "rb") as f:
                    body = f.read()
            except OSError:
                return jsend(self, {"ok": False, "error": "读输出失败"}, 404)
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Disposition",
                             'attachment; filename="%s"' % os.path.basename(out))
            self.end_headers()
            self.wfile.write(body)
            return
        return self.send_error(404)

    def _do_upload(self):
        # 面板文件入口：浏览器选文件 → 内容 POST 到服务端 → 落 sample_dir。
        # （老逻辑 FileReader 本地加载跨不过 WSL/Windows 文件系统边界。）
        # {type: scene|replay|addrmap, text: JSON字符串} → 写 sample_dir 固定名。
        try:
            n = int(self.headers.get("Content-Length") or 0)
            req = json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception as e:
            return jsend(self, {"ok": False, "error": f"请求解析失败: {e}"})
        kind = req.get("type")
        if kind == "script":
            # 剧本 txt：浏览器 UTF-8 → 服务端转 GBK 落 script_path()。
            sp = script_path()
            if not sp:
                return jsend(self, {"ok": False,
                                    "error": "服务端未配剧本路径（配置区填 script 或配 --simple-dir）"})
            try:
                data = (req.get("text") or "").encode("gbk")
            except Exception as e:
                return jsend(self, {"ok": False, "error": f"转 GBK 失败: {e}"})
            try:
                os.makedirs(os.path.dirname(os.path.abspath(sp)), exist_ok=True)
                tmp = sp + ".up-tmp"
                with open(tmp, "wb") as f:
                    f.write(data)
                os.replace(tmp, sp)
            except Exception as e:
                return jsend(self, {"ok": False, "error": f"写盘失败: {e}"})
            reset_instant()
            return jsend(self, {"ok": True, "path": sp,
                                "lines": (req.get("text") or "").count("\n") + 1})
        names = {"scene": "scene.json", "replay": "replay.json",
                 "addrmap": "addrmap.json"}
        if kind not in names:
            return jsend(self, {"ok": False,
                                "error": "type 须为 scene|replay|addrmap|script"})
        try:
            obj = json.loads(req.get("text") or "")
        except Exception:
            return jsend(self, {"ok": False, "error": "不是合法 JSON"})
        if kind == "scene" and not (isinstance(obj, dict) and obj.get("prims")):
            return jsend(self, {"ok": False, "error": "scene 缺 prims"})
        if kind == "replay" and not (isinstance(obj, dict) and obj.get("rows")):
            return jsend(self, {"ok": False, "error": "replay 缺 rows"})
        if kind == "addrmap" and not (isinstance(obj, dict) and obj.get("addrmap")):
            return jsend(self, {"ok": False, "error": "addrmap 缺 addrmap"})
        dst = os.path.join(C("sample_dir"), names[kind])
        try:
            os.makedirs(C("sample_dir"), exist_ok=True)
            tmp = dst + ".up-tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(obj, f, ensure_ascii=False)
            os.replace(tmp, dst)  # 原子换位，读一半的 torn JSON 不落地
        except Exception as e:
            return jsend(self, {"ok": False, "error": f"写盘失败: {e}"})
        out = {"ok": True, "path": dst}
        if kind == "replay":
            out["rows"] = len(obj.get("rows", []))
        return jsend(self, out)

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        if u.path == "/api/instant":
            # P3: 即时预览 {text, line} → 编译(如需)+break-pc 快照。同步等（秒级）。
            try:
                n = int(self.headers.get("Content-Length") or 0)
                req = json.loads(self.rfile.read(n).decode("utf-8"))
            except Exception as e:
                return jsend(self, {"ok": False, "error": f"请求解析失败: {e}"})
            text, L = req.get("text"), req.get("line")
            if text is None or L is None:
                return jsend(self, {"ok": False, "error": "缺 text/line"})
            sp = script_path()
            sd = C("simple_dir")
            if not sp or not sd or not os.path.isdir(sd):
                return jsend(self, {"ok": False, "error": "服务端未配 --simple-dir/--script"})
            try:
                L = int(L)
            except Exception:
                return jsend(self, {"ok": False, "error": "line 非整数"})
            try:
                with open(sp, "wb") as f:
                    f.write(text.encode("gbk"))
            except Exception as e:
                return jsend(self, {"ok": False, "error": f"写剧本失败: {e}"})
            try:
                return jsend(self, run_instant(sd, sp, L))
            except RuntimeError as e:
                return jsend(self, {"ok": False, "error": str(e)})
            except Exception as e:
                return jsend(self, {"ok": False, "error": f"即时预览异常: {e}"})
        if u.path == "/api/rebuild":
            # P2: 存剧本 → 后台重跑管线。{text, ticks?} → {ok, job}。
            try:
                n = int(self.headers.get("Content-Length") or 0)
                req = json.loads(self.rfile.read(n).decode("utf-8"))
            except Exception as e:
                return jsend(self, {"ok": False, "error": f"请求解析失败: {e}"})
            text = req.get("text")
            if text is None:
                return jsend(self, {"ok": False, "error": "缺 text"})
            sp = script_path()
            sd = C("simple_dir")
            if not sp or not sd or not os.path.isdir(sd):
                return jsend(self, {"ok": False, "error": "服务端未配 --simple-dir/--script"})
            try:
                ticks = int(req.get("ticks") or 1500)
            except Exception:
                ticks = 1500
            try:
                with open(sp, "wb") as f:
                    f.write(text.encode("gbk"))
            except Exception as e:
                return jsend(self, {"ok": False, "error": f"写剧本失败: {e}"})
            with JOBS_LOCK:
                JOB_SEQ[0] += 1
                job = f"r{JOB_SEQ[0]}"
                JOBS[job] = {"state": "running", "log": "", "t0": time.time()}
            t = threading.Thread(target=run_rebuild,
                                 args=(job, sd, sp, C("sample_dir"), ticks, ARGS.port),
                                 daemon=True)
            t.start()
            return jsend(self, {"ok": True, "job": job})
        if u.path == "/api/config":
            # 工程配置热切换：{values: {key: path}} 原子校验→应用→落盘。
            try:
                n = int(self.headers.get("Content-Length") or 0)
                req = json.loads(self.rfile.read(n).decode("utf-8"))
            except Exception as e:
                return jsend(self, {"ok": False, "error": f"请求解析失败: {e}"})
            vals = req.get("values") or {}
            unknown = [k for k in vals if k not in CONFIG_KEYS]
            if unknown:
                return jsend(self, {"ok": False,
                                    "error": f"未知配置项: {','.join(unknown)}"})
            norm = {k: (vals[k] or "").strip() for k in vals}
            # 先全量校验（含 out_hcb↔hcb 互斥：用“新 hcb”复核）
            errs = {}
            for k, v in norm.items():
                ok, msg = config_check(k, v)
                if not ok:
                    errs[k] = msg
            # 最终态互斥：新 hcb 不能等于最终 out（含默认派生名）。
            fh = norm.get("hcb", C("hcb"))
            fo = norm.get("out_hcb", C("out_hcb")) or (
                fh + ".edit.hcb" if fh else "")
            if fh and fo and os.path.abspath(fh) == os.path.abspath(fo):
                errs["out_hcb" if "out_hcb" in norm else "hcb"] = \
                    "hcb 与输出路径相同，拒绝覆盖原文件"
            if errs:
                return jsend(self, {"ok": False, "errors": errs})
            # script → simple_dir 自动反推：<simple>/base/Script.txt 且
            # simple 未配（也没随包新配）时，省掉一次手工填。
            derived = {}
            if norm.get("script") and not norm.get("simple_dir", C("simple_dir")):
                ap = os.path.abspath(norm["script"])
                if os.path.basename(ap) == "Script.txt" and \
                        os.path.basename(os.path.dirname(ap)) == "base":
                    cand = os.path.dirname(os.path.dirname(ap))
                    if os.path.isdir(cand):
                        norm["simple_dir"] = cand
                        derived["simple_dir"] = cand
            changed = {k for k, v in norm.items() if v != C(k)}
            CONFIG.update(norm)
            if C("fvp_base"):
                os.environ["FVP_BASE_PATH"] = C("fvp_base")
            elif "fvp_base" in changed:
                os.environ.pop("FVP_BASE_PATH", None)
            if changed & {"simple_dir", "script", "sample_dir"}:
                reset_instant()
            save_config()
            out = {"ok": True, "changed": sorted(changed)}
            if derived:
                out["derived"] = derived
            return jsend(self, out)
        if u.path == "/api/export-hcb":
            # 导出 txt 的编译版 hcb（走 hcb_build 产物 .test.chb，无需原 hcb）。
            try:
                n = int(self.headers.get("Content-Length") or 0)
                req = json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
            except Exception as e:
                return jsend(self, {"ok": False, "error": f"请求解析失败: {e}"})
            sp = script_path()
            sd = C("simple_dir")
            if not sp or not sd or not os.path.isdir(sd):
                return jsend(self, {"ok": False,
                                    "error": "服务端未配剧本（配置区填剧本txt）"})
            text = req.get("text")
            if text is not None:
                try:
                    with open(sp, "wb") as f:
                        f.write(text.encode("gbk"))
                except Exception as e:
                    return jsend(self, {"ok": False, "error": f"写剧本失败: {e}"})
            try:
                build_simple(sd, sp)
            except RuntimeError as e:
                return jsend(self, {"ok": False, "error": str(e)})
            except Exception as e:
                return jsend(self, {"ok": False, "error": f"导出失败: {e}"})
            try:
                with BUILD_LOCK:
                    with open(os.path.join(sd, ".test.chb"), "rb") as f:
                        body = f.read()
            except OSError:
                return jsend(self, {"ok": False, "error": "构建产物缺失"})
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Disposition",
                             'attachment; filename="export.hcb"')
            self.end_headers()
            self.wfile.write(body)
            return
        if u.path == "/api/upload":
            return self._do_upload()
        if u.path != "/api/writeback":
            return self.send_error(404)
        try:
            n = int(self.headers.get("Content-Length") or 0)
            req = json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception as e:
            return jsend(self, {"ok": False, "error": f"请求解析失败: {e}"})
        edits = req.get("edits") or []
        hcb = req.get("hcb") or C("hcb")
        if not edits:
            return jsend(self, {"ok": False, "error": "无改动"})
        if not hcb or not os.path.isfile(hcb):
            return jsend(self, {"ok": False, "error": "HCB 路径无效（服务端 --hcb 或页面填写）"})
        am = C("addrmap") or os.path.join(C("sample_dir"), "addrmap.json")
        if not os.path.isfile(am):
            return jsend(self, {"ok": False, "error": f"映射表不存在: {am}"})
        out = resolve_out(hcb)
        if os.path.abspath(out) == os.path.abspath(hcb):
            return jsend(self, {"ok": False, "error": "拒绝覆盖原文件，请换 --out-hcb"})
        cmd = [sys.executable, os.path.join(REPO, "patch_hcb.py"), hcb, am, out]
        for e in edits:
            cmd.append(f"{e['id']}:{e['field']}={e['value']}")
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        except Exception as e:
            return jsend(self, {"ok": False, "error": f"补丁执行失败: {e}"})
        tail = (r.stdout + r.stderr).strip().splitlines()
        tail = tail[-1] if tail else ""
        if r.returncode != 0:
            return jsend(self, {"ok": False, "error": tail or "patch_hcb 报错"})
        return jsend(self, {"ok": True, "out": out, "log": tail})


def main():
    global ARGS
    ap = argparse.ArgumentParser()
    # 路径类默认 None：未传才走 server_config.json（显式 > 文件 > 默认）。
    ap.add_argument("--sample-dir", default=None)
    ap.add_argument("--hcb", default=None)
    ap.add_argument("--addrmap", default=None)
    ap.add_argument("--out-hcb", default=None)
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--simple-dir", default=None,
                    help="P2: Simple 仓库目录（开剧本编辑+重跑需配）")
    ap.add_argument("--script", default=None,
                    help="P2: 剧本 txt 路径（磁盘 GBK；默认 <simple-dir>/base/Script.txt）")
    ap.add_argument("--fvp-base", default=None,
                    help="游戏资源目录（默认读环境 FVP_BASE_PATH；网页配置区可改）")
    ap.add_argument("--host", default="0.0.0.0",
                    help="监听地址（WSL2 下 Windows 浏览器需 0.0.0.0，用 WSL IP 访问）")
    ARGS = ap.parse_args()
    load_config(ARGS)
    srv = ThreadingHTTPServer((ARGS.host, ARGS.port), H)
    print(f"编辑器服务 http://localhost:{ARGS.port}/（Ctrl+C 停）", flush=True)
    print(f"sample={C('sample_dir')} hcb={C('hcb') or '(页面填)'}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
