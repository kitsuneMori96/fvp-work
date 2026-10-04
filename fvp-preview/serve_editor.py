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
import subprocess
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(HERE, "web")
REPO = HERE
ARGS = None


def jsend(h, obj, code=200):
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    h.send_response(code)
    h.send_header("Content-Type", "application/json; charset=utf-8")
    h.send_header("Content-Length", str(len(body)))
    h.end_headers()
    h.wfile.write(body)


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
            return jsend(self, {"hcb": ARGS.hcb or "", "sample_dir": ARGS.sample_dir})
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
    ARGS = ap.parse_args()
    srv = ThreadingHTTPServer(("127.0.0.1", ARGS.port), H)
    print(f"编辑器服务 http://localhost:{ARGS.port}/（Ctrl+C 停）", flush=True)
    print(f"sample={ARGS.sample_dir} hcb={ARGS.hcb or '(页面填)'}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
