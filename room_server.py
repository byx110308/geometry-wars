#!/usr/bin/env python3
"""
多用途服务器 — 房间列表 + 因子信号 API
========================================
房间接口:  GET /rooms, POST /register, /unregister, /refresh
因子接口:  POST /signals  → 返回当日四因子选股信号
           GET  /health   → 模型状态
"""

import json
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

import numpy as np
import pandas as pd

# ============================================
# 因子模型（从 generate_signals.py 内联）
# ============================================
DATA_DIR = "/mnt/d/HuaweiMoveData/Users/胤羲/Desktop/distribute/distribute"


def _load_panels():
    dr = pd.read_parquet(f"{DATA_DIR}/stock/daily_returns.parquet")
    dr["stkcd"] = dr["stkcd"].astype(str).str.zfill(6)
    dr["trddt"] = pd.to_datetime(dr["trddt"])
    dr = dr[dr["dnshrtrd"] > 0]

    def _p(col):
        p = dr.pivot_table(index="trddt", columns="stkcd", values=col, aggfunc="last")
        return p.sort_index()

    return {
        "dretwd": _p("dretwd"),
        "clsprc": _p("clsprc"),
        "limitstatus": _p("limitstatus"),
        "dsmvtll": _p("dsmvtll"),
        "dnvaltrd": _p("dnvaltrd"),
        "dates": sorted(dr["trddt"].unique()),
    }


def _zs(arr):
    arr = np.asarray(arr, dtype=float)
    mask = np.isfinite(arr)
    if not mask.any() or arr[mask].std() == 0:
        return np.zeros_like(arr)
    mu, sd = arr[mask].mean(), arr[mask].std()
    z = np.zeros_like(arr)
    z[mask] = np.clip((arr[mask] - mu) / sd, -3, 3)
    return z


def compute_signals(panels, date, top_k=50):
    """
    给定日期，返回四因子综合得分 + top_k 推荐股票。
    """
    date = pd.Timestamp(date)
    if date not in panels["dates"]:
        return None, f"日期 {date.date()} 无行情数据"

    # 取最近 120 天
    idx = panels["dates"]
    pos = idx.index(date)
    if pos < 60:
        return None, f"日期 {date.date()} 历史数据不足"
    window = idx[max(0, pos - 119) : pos + 1]

    rp = panels["dretwd"].loc[window]
    valid = rp.columns[rp.iloc[-120:].count() >= 60]

    # 流动性过滤
    ap = panels["dnvaltrd"].loc[window]
    if len(ap) >= 20:
        avg_amt = ap.iloc[-20:].mean()
        valid = valid[valid.isin(avg_amt[avg_amt >= 2e7].index)]

    # 市值过滤
    mp = panels["dsmvtll"].loc[window]
    if len(mp) > 0:
        mcap = mp.iloc[-1]
        valid = valid[valid.isin(mcap[mcap >= 2_000_000].dropna().index)]

    if len(valid) < 10:
        return None, "候选股太少"

    codes = list(valid)
    rp = rp.reindex(columns=codes, fill_value=0.0)
    arr = rp.values
    n = len(codes)

    # 四因子
    f1 = _zs(arr[-1, :])
    s20 = arr[-20:, :].std(axis=0)
    s20[s20 == 0] = 1.0
    f2 = -_zs(s20)
    mx20 = arr[-20:, :].max(axis=0)
    f3 = -_zs(mx20)
    r60 = arr[-60:, :].sum(axis=0) if len(arr) >= 60 else arr.sum(axis=0)
    f4 = -_zs(r60)

    score = (f1 + f2 + f3 + f4) * 0.25
    df = pd.DataFrame({
        "stkcd": codes,
        "score": score,
        "mom1": f1,
        "lowvol": f2,
        "maxavd": f3,
        "rev60": f4,
    }).sort_values("score", ascending=False)

    top = df.head(top_k)
    return {
        "date": str(date.date()),
        "n_universe": n,
        "top_k": top_k,
        "stocks": top[["stkcd", "score"]].to_dict("records"),
        "factors": top[["stkcd", "mom1", "lowvol", "maxavd", "rev60"]].to_dict("records"),
    }, None


# ============================================
# 房间管理
# ============================================
ROOMS = {}
TIMEOUT = 300

# ============================================
# HTTP 服务
# ============================================
HOST = "0.0.0.0"
PORT = 8765


class Handler(BaseHTTPRequestHandler):
    panels = None  # 类级别共享

    def _json(self, data, code=200):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False, default=str).encode())

    def do_OPTIONS(self):
        self._json({}, 204)

    def do_GET(self):
        path = urlparse(self.path).path

        if path == "/rooms":
            now = time.time()
            for k in list(ROOMS):
                if now - ROOMS[k]["time"] > TIMEOUT:
                    del ROOMS[k]
            self._json(list(ROOMS.values()))

        elif path == "/health":
            self._json({
                "status": "ok",
                "model_loaded": self.panels is not None,
                "rooms": len(ROOMS),
            })

        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        path = urlparse(self.path).path

        try:
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length)) if length > 0 else {}
        except Exception:
            self._json({"error": "invalid json"}, 400)
            return

        # ---- 房间接口 ----
        if path == "/register":
            code = data.get("code", "").strip().upper()
            if not code:
                return self._json({"error": "missing code"}, 400)
            ROOMS[code] = {
                "code": code,
                "host": data.get("name", "?"),
                "players": data.get("players", 1),
                "time": time.time(),
            }
            self._json({"ok": True})

        elif path == "/unregister":
            code = data.get("code", "").strip().upper()
            ROOMS.pop(code, None)
            self._json({"ok": True})

        elif path == "/refresh":
            code = data.get("code", "").strip().upper()
            if code in ROOMS:
                ROOMS[code]["time"] = time.time()
                self._json({"ok": True})
            else:
                self._json({"error": "room not found"}, 404)

        # ---- 因子接口 ----
        elif path == "/signals":
            if self.panels is None:
                return self._json({"error": "模型未加载"}, 503)

            date_str = data.get("date", "")
            top_k = data.get("top_k", 30)

            if not date_str:
                # 默认取最新交易日
                date_str = str(self.panels["dates"][-1].date())

            result, err = compute_signals(self.panels, date_str, top_k)
            if err:
                self._json({"error": err}, 400)
            else:
                self._json(result)

        else:
            self._json({"error": "not found"}, 404)

    def log_message(self, format, *args):
        """精简日志"""
        if "/signals" in format or "/register" in format:
            sys.stderr.write(f"[{time.strftime('%H:%M:%S')}] {format % args}\n")


# ============================================
# 入口
# ============================================
if __name__ == "__main__":
    import sys

    print("=" * 55)
    print("  多用途服务器 — 房间列表 + 四因子信号 API")
    print("=" * 55)

    # 加载行情数据
    print("[1/2] 加载行情数据...")
    try:
        Handler.panels = _load_panels()
        print(f"       {len(Handler.panels['dates'])} 天 × {len(Handler.panels['dretwd'].columns)} 股")
    except Exception as e:
        print(f"       [警告] 行情加载失败: {e}")
        print(f"       房间列表仍可用，/signals 将不可用")

    # 启动服务器
    print(f"[2/2] 启动: http://{HOST}:{PORT}")
    print(f"       房间: GET /rooms  | POST /register /unregister /refresh")
    print(f"       因子: POST /signals" + ' {"date": "2022-12-30"}  | GET /health')

    server = HTTPServer((HOST, PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务器已停止")
        server.server_close()
