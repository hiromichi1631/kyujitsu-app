"""休日管理ツール 同期サーバー

静的ファイル配信 + データ同期API（Python標準ライブラリのみ）。
合言葉のハッシュ(X-Sync-Key)ごとにJSONを1ファイル保存する。

  GET /api/data  : 保存済みデータを返す（未保存なら404）
  PUT /api/data  : データを保存する  body: {"data": {...}, "updatedAt": "ISO8601"}
"""
import hashlib
import json
import os
import re
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("DATA_DIR", ROOT / "data"))
PORT = int(os.environ.get("PORT", "8735"))
MAX_BODY = 1_000_000  # 1MB（個人の休日データには十分すぎる上限）

KEY_RE = re.compile(r"[0-9a-f]{64}")


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    # ---- helpers ----
    def _account_path(self):
        """合言葉ハッシュを検証し、保存先ファイルパスを返す（不正ならNone）"""
        key = self.headers.get("X-Sync-Key", "")
        if not KEY_RE.fullmatch(key):
            return None
        # クライアントから届くのは既にSHA-256だが、サーバー側でもう一度
        # ハッシュしてファイル名にする（キー文字列をそのまま残さない）
        return DATA_DIR / (hashlib.sha256(key.encode()).hexdigest() + ".json")

    def _send_json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ---- API ----
    def do_GET(self):
        if self.path.split("?")[0] == "/api/data":
            path = self._account_path()
            if path is None:
                return self._send_json(400, {"error": "invalid sync key"})
            if not path.exists():
                return self._send_json(404, {"error": "no data"})
            try:
                return self._send_json(200, json.loads(path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                return self._send_json(500, {"error": "read failed"})
        super().do_GET()

    def do_PUT(self):
        if self.path.split("?")[0] != "/api/data":
            return self._send_json(404, {"error": "not found"})
        path = self._account_path()
        if path is None:
            return self._send_json(400, {"error": "invalid sync key"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_BODY:
            return self._send_json(413, {"error": "bad size"})
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return self._send_json(400, {"error": "invalid json"})
        if not isinstance(payload, dict) or "data" not in payload or "updatedAt" not in payload:
            return self._send_json(400, {"error": "invalid format"})
        try:
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            tmp.replace(path)  # 書き込み途中で落ちても壊れないよう原子的に置換
        except OSError:
            return self._send_json(500, {"error": "write failed"})
        return self._send_json(200, {"ok": True, "updatedAt": payload["updatedAt"]})

    def log_message(self, fmt, *args):
        # APIアクセスのみ記録（静的ファイルのログは省く）
        if self.path.startswith("/api/"):
            super().log_message(fmt, *args)


if __name__ == "__main__":
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"listening on :{PORT}  data={DATA_DIR}")
    server.serve_forever()
