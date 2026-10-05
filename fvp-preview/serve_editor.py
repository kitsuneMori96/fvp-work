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
            if not p or not os.path.isfile(p):
                return self.send_error(404, "贴图不存在")
            ext = os.path.splitext(p)[1].lower()
            ctype = {"png": "image/png", "jpg": "image/jpeg", "bmp": "image/bmp"}.get(
                ext.lstrip("."), "application/octet-stream")
            return self._file(p, ctype)
        return self.send_error(404)

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
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
