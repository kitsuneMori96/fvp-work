#!/usr/bin/env python3
"""编辑器微服务（标准库 only）：静态页 + 场景/回放/贴图 + 一键写回。

用法：
  python3 serve_editor.py --sample-dir /tmp/fvp-sample [--hcb 原.hcb]
      [--addrmap traced.json] [--out-hcb 新.hcb] [--port 8000]
  浏览器开 http://localhost:8000 →「载入示例工程」。

写回安全：只调 patch_hcb.py（等宽/操作码/旧值三校验），默认绝不覆盖原文件
（必须显式 --out-hcb）。
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(HERE, "web")
REPO = HERE
ARGS = None

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
        _sh.copy(script_txt, os.path.join(simple_dir, "base", "Script.txt"))
        blog = os.path.join(simple_dir, "base", "build.log")
        p = subprocess.run(["python3", "hcb_build.py"], cwd=simple_dir,
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
    entry = _derive_entry(os.path.join(simple_dir, "base", "base.chb"), chb)
    INSTANT["text_hash"] = digest
    INSTANT["entry"] = entry
    INSTANT["linemap"] = lm
    # 全量回放的行→tick 提示（有则断点 ticks 更准）
    INSTANT["line_tick"] = {}
    try:
        rj = json.load(open(os.path.join(ARGS.sample_dir, "replay.json"), encoding="utf-8"))
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
    # FVP_VM_BIN: release 二进制更快（debug 约 40t/s，分钟级跑满长剧本才需要）。
    vm = os.environ.get("FVP_VM_BIN",
                        os.path.expanduser("~/.cache/cargo-target/fvp-preview/debug/fvp-preview-vm"))
    entry, lm = build_simple(simple_dir, script_txt)
    target = line_target(lm, L)
    hint = INSTANT["line_tick"].get(L)
    cap = (hint + 150) if hint else 2000
    idir = ARGS.sample_dir.rstrip("/") + ".instant"
    os.makedirs(os.path.join(idir, "tex"), exist_ok=True)
    # 拷一份 chb 再跑：全量重建会重写 .test.chb，直接读会撕裂。
    chb_run = os.path.join(idir, "instant.chb")
    with BUILD_LOCK:
        import shutil as _sh2
        _sh2.copy(os.path.join(simple_dir, ".test.chb"), chb_run)
    fbh = os.environ.get("FVP_BASE_PATH", "")
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
    if ARGS.script:
        return ARGS.script
    if ARGS.simple_dir:
        return os.path.join(ARGS.simple_dir, "base", "Script.txt")
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
            return self._file(os.path.join(ARGS.sample_dir, "scene.json"), "application/json")
        if u.path == "/api/replay":
            return self._file(os.path.join(ARGS.sample_dir, "replay.json"), "application/json")
        if u.path == "/api/addrmap":
            ap = ARGS.addrmap or os.path.join(ARGS.sample_dir, "addrmap.json")
            return self._file(ap, "application/json")
        if u.path == "/api/paths":
            return jsend(self, {"hcb": ARGS.hcb or "", "sample_dir": ARGS.sample_dir,
                                "simple_dir": ARGS.simple_dir or "",
                                "script": script_path()})
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
                p = os.path.join(ARGS.sample_dir, p)
            p = os.path.normpath(p)
            # 允许 sample_dir 本体 + 即时预览用的 <sample>.instant 兄弟目录
            #（instant 快照里是 --png-dir 落盘的绝对路径）。
            roots = [os.path.normpath(ARGS.sample_dir),
                     os.path.normpath(ARGS.sample_dir.rstrip("/") + ".instant")]
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
        return self.send_error(404)

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
            sd = ARGS.simple_dir
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
            sd = ARGS.simple_dir
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
                                 args=(job, sd, sp, ARGS.sample_dir, ticks, ARGS.port),
                                 daemon=True)
            t.start()
            return jsend(self, {"ok": True, "job": job})
        if u.path != "/api/writeback":
            return self.send_error(404)
        try:
            n = int(self.headers.get("Content-Length") or 0)
            req = json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception as e:
            return jsend(self, {"ok": False, "error": f"请求解析失败: {e}"})
        edits = req.get("edits") or []
        hcb = req.get("hcb") or ARGS.hcb
        if not edits:
            return jsend(self, {"ok": False, "error": "无改动"})
        if not hcb or not os.path.isfile(hcb):
            return jsend(self, {"ok": False, "error": "HCB 路径无效（服务端 --hcb 或页面填写）"})
        am = ARGS.addrmap or os.path.join(ARGS.sample_dir, "addrmap.json")
        if not os.path.isfile(am):
            return jsend(self, {"ok": False, "error": f"映射表不存在: {am}"})
        out = ARGS.out_hcb or (hcb + ".edit.hcb")
        if os.path.abspath(out) == os.path.abspath(hcb):
            return jsend(self, {"ok": False, "error": "拒绝覆盖原文件，请换 --out-hcb"})
        cmd = ["python3", os.path.join(REPO, "patch_hcb.py"), hcb, am, out]
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
    ap.add_argument("--sample-dir", default="/tmp/fvp-sample")
    ap.add_argument("--hcb", default="")
    ap.add_argument("--addrmap", default="")
    ap.add_argument("--out-hcb", default="")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--simple-dir", default="",
                    help="P2: Simple 仓库目录（开剧本编辑+重跑需配）")
    ap.add_argument("--script", default="",
                    help="P2: 剧本 txt 路径（磁盘 GBK；默认 <simple-dir>/base/Script.txt）")
    ap.add_argument("--host", default="0.0.0.0",
                    help="监听地址（WSL2 下 Windows 浏览器需 0.0.0.0，用 WSL IP 访问）")
    ARGS = ap.parse_args()
    srv = ThreadingHTTPServer((ARGS.host, ARGS.port), H)
    print(f"编辑器服务 http://localhost:{ARGS.port}/（Ctrl+C 停）", flush=True)
    print(f"sample={ARGS.sample_dir} hcb={ARGS.hcb or '(页面填)'}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
