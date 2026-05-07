#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股盯盘软件 - 悬浮窗版
"""

import ctypes
import datetime
import json
import queue
import threading
import time
import tkinter as tk
import tkinter.messagebox as messagebox
import tkinter.simpledialog as simpledialog
import tkinter.ttk as ttk
from tkinter import font as tkfont
from urllib import request

CONFIG_FILE = "config.json"
MAX_STOCKS_RECOMMENDED = 10

# ============ 配色 ============
THEME = {
    "bg": "#0c0e15",
    "card": "#13161f",
    "border": "#1e2230",
    "fg": "#d1d5db",
    "fg2": "#6b7280",
    "accent": "#3b82f6",
    "accent_hover": "#2563eb",
    "up": "#ef4444",
    "down": "#22c55e",
    "btn_primary": "#3b82f6",
    "btn_primary_hover": "#2563eb",
    "btn_secondary": "#1e2230",
    "btn_secondary_hover": "#2a2f3d",
    "input_bg": "#0c0e15",
    "chart_bg": "#0c0e15",
    "grid": "#1e2230",
}

# ============ 配置 ============
def load_config():
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"加载配置失败: {e}, 使用默认配置")
        return {
            "stocks": [{"code": "600519", "market": "sh", "name": "贵州茅台"}],
            "window": {"x": 100, "y": 100, "refresh_interval": 3000},
            "ui": {"scale": 1.0, "alpha": 0.95, "show_weibi": True, "show_wudang": True},
        }


def save_config(config):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=4)
        return True
    except Exception as e:
        print(f"保存配置失败: {e}")
        return False


def auto_detect_market(code):
    if not code or len(code) != 6 or not code.isdigit():
        return None
    if code.startswith(("600", "601", "603", "605", "688")):
        return "sh"
    if code.startswith(("000", "001", "002", "003", "300", "301")):
        return "sz"
    if code.startswith(("4", "8")):
        return "bj"
    return None


def is_trading_time():
    now = datetime.datetime.now()
    if now.weekday() >= 5:
        return False
    hm = now.hour * 100 + now.minute
    return (915 <= hm <= 1130) or (1300 <= hm <= 1500)


# ============ 请求限流 ============
class RequestLimiter:
    def __init__(self):
        self.lock = threading.Lock()
        self.last_request = {}

    def can_request(self, key, min_interval=5):
        with self.lock:
            now = time.time()
            if key not in self.last_request or now - self.last_request[key] >= min_interval:
                self.last_request[key] = now
                return True
            return False


# ============ 全局热键（老板键） ============
VK_MAP = {
    "A": 0x41, "B": 0x42, "C": 0x43, "D": 0x44, "E": 0x45, "F": 0x46,
    "G": 0x47, "H": 0x48, "I": 0x49, "J": 0x4A, "K": 0x4B, "L": 0x4C,
    "M": 0x4D, "N": 0x4E, "O": 0x4F, "P": 0x50, "Q": 0x51, "R": 0x52,
    "S": 0x53, "T": 0x54, "U": 0x55, "V": 0x56, "W": 0x57, "X": 0x58,
    "Y": 0x59, "Z": 0x5A,
    "0": 0x30, "1": 0x31, "2": 0x32, "3": 0x33, "4": 0x34,
    "5": 0x35, "6": 0x36, "7": 0x37, "8": 0x38, "9": 0x39,
    "F1": 0x70, "F2": 0x71, "F3": 0x72, "F4": 0x73, "F5": 0x74,
    "F6": 0x75, "F7": 0x76, "F8": 0x77, "F9": 0x78, "F10": 0x79,
    "F11": 0x7A, "F12": 0x7B,
    "ESC": 0x1B, "SPACE": 0x20, "TAB": 0x09, "ENTER": 0x0D,
    "BACKSPACE": 0x08, "DELETE": 0x2E, "INSERT": 0x2D,
    "HOME": 0x24, "END": 0x23, "PAGEUP": 0x21, "PAGEDOWN": 0x22,
    "UP": 0x26, "DOWN": 0x28, "LEFT": 0x25, "RIGHT": 0x27,
    "PRINT": 0x2C, "SCROLL": 0x91, "PAUSE": 0x13,
}

MOD_MAP = {
    "CTRL": 0x0002, "ALT": 0x0001, "SHIFT": 0x0004, "WIN": 0x0008,
}


def parse_hotkey(hotkey_str):
    """解析快捷键字符串，返回 (modifiers, vk)"""
    if not hotkey_str:
        return None, None
    parts = [p.strip().upper() for p in hotkey_str.split("+")]
    modifiers = 0
    vk = 0
    key_part = None
    for p in parts:
        if p in MOD_MAP:
            modifiers |= MOD_MAP[p]
        elif p in VK_MAP:
            key_part = p
            vk = VK_MAP[p]
        elif len(p) == 1:
            key_part = p
            vk = ord(p)
    if not key_part:
        return None, None
    # 必须至少有一个修饰键，防止和普通输入冲突
    if modifiers == 0:
        modifiers = MOD_MAP["CTRL"]  # 默认加 Ctrl
    return modifiers, vk


class GlobalHotkey:
    """基于 GetAsyncKeyState 的全局热键监听（Windows 纯标准库实现）"""
    def __init__(self):
        self.hotkeys = []  # [(modifiers, vk, callback)]
        self.running = False
        self.thread = None
        self._prev_states = {}

    def register(self, modifiers, vk, callback):
        if modifiers is None or vk is None:
            return False
        self.hotkeys.append((modifiers, vk, callback))
        return True

    def clear(self):
        self.hotkeys.clear()
        self._prev_states.clear()

    def start(self):
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False

    def _loop(self):
        user32 = ctypes.windll.user32
        while self.running:
            for mods, vk, cb in self.hotkeys:
                # 检测修饰键
                ctrl = user32.GetAsyncKeyState(0x11) & 0x8000
                alt = user32.GetAsyncKeyState(0x12) & 0x8000
                shift = user32.GetAsyncKeyState(0x10) & 0x8000
                win = user32.GetAsyncKeyState(0x5B) & 0x8000

                mods_ok = True
                if mods & 0x0002 and not ctrl:
                    mods_ok = False
                if mods & 0x0001 and not alt:
                    mods_ok = False
                if mods & 0x0004 and not shift:
                    mods_ok = False
                if mods & 0x0008 and not win:
                    mods_ok = False

                key_pressed = user32.GetAsyncKeyState(vk) & 0x8000
                state_key = (mods, vk)
                prev = self._prev_states.get(state_key, False)

                # 只在按下瞬间触发一次（防止长按重复触发）
                if mods_ok and key_pressed and not prev:
                    try:
                        cb()
                    except Exception as e:
                        print(f"老板键回调错误: {e}")

                self._prev_states[state_key] = mods_ok and key_pressed

            time.sleep(0.05)


# ============ 技术指标 ============
def ema(values, n):
    if not values or n <= 0:
        return []
    multiplier = 2 / (n + 1)
    result = [values[0]]
    for v in values[1:]:
        result.append(v * multiplier + result[-1] * (1 - multiplier))
    return result


def calc_macd(closes):
    if len(closes) < 36:
        return [], [], []
    ema12 = ema(closes, 12)
    ema26 = ema(closes, 26)
    dif = [ema12[i] - ema26[i] for i in range(len(closes))]
    dea = ema(dif, 9)
    macd = [2 * (dif[i] - dea[i]) for i in range(len(closes))]
    return dif, dea, macd


def find_bottoms(values, window=3):
    bottoms = []
    for i in range(window, len(values) - window):
        is_bottom = True
        for j in range(1, window + 1):
            if values[i] > values[i - j] or values[i] > values[i + j]:
                is_bottom = False
                break
        if is_bottom:
            bottoms.append(i)
    return bottoms


def find_peaks(values, window=3):
    peaks = []
    for i in range(window, len(values) - window):
        is_peak = True
        for j in range(1, window + 1):
            if values[i] < values[i - j] or values[i] < values[i + j]:
                is_peak = False
                break
        if is_peak:
            peaks.append(i)
    return peaks


def detect_bottom_divergence(closes, macd_vals, window=3):
    if len(closes) < 10 or len(macd_vals) < 10:
        return False
    bottoms = find_bottoms(closes, window)
    if len(bottoms) < 2:
        return False
    i1, i2 = bottoms[-2], bottoms[-1]
    if closes[i2] < closes[i1] and macd_vals[i2] > macd_vals[i1]:
        return True
    return False


def detect_top_divergence(closes, macd_vals, window=3):
    if len(closes) < 10 or len(macd_vals) < 10:
        return False
    peaks = find_peaks(closes, window)
    if len(peaks) < 2:
        return False
    i1, i2 = peaks[-2], peaks[-1]
    if closes[i2] > closes[i1] and macd_vals[i2] < macd_vals[i1]:
        return True
    return False


# ============ 数据获取 ============
class StockData:
    limiter = RequestLimiter()

    @staticmethod
    def fetch_realtime(stocks):
        codes = []
        for s in stocks:
            market = s.get("market", "")
            code = s["code"]
            if not market:
                market = auto_detect_market(code) or "sh"
            codes.append(f"{market}{code}")
        if not codes:
            return {}

        url = f"http://qt.gtimg.cn/q={','.join(codes)}"
        try:
            req = request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with request.urlopen(req, timeout=10) as resp:
                data = resp.read().decode("gbk", errors="ignore")
        except Exception as e:
            print(f"获取实时行情失败: {e}")
            return {}

        result = {}
        for line in data.strip().split(";"):
            line = line.strip()
            if not line or "=" not in line:
                continue
            parts = line.split("=")
            if len(parts) < 2:
                continue
            code_key = parts[0].replace("v_", "").strip()
            content = parts[1].strip().strip('"')
            if not content:
                continue
            fields = content.split("~")
            if len(fields) < 50:
                continue

            def fval(idx, default=""):
                try:
                    return float(fields[idx])
                except (ValueError, IndexError):
                    return default

            name = fields[1]
            code = fields[2]
            price = fval(3, 0)
            pre_close = fval(4, 0)
            change = fval(31, 0)
            change_pct = fval(32, 0)

            # 五档行情
            wudang = {
                "buy": [],
                "sell": [],
            }
            for i in range(5):
                b_price = fval(9 + i * 2, 0)
                b_vol = int(fval(10 + i * 2, 0))
                s_price = fval(19 + i * 2, 0)
                s_vol = int(fval(20 + i * 2, 0))
                wudang["buy"].append({"price": b_price, "volume": b_vol})
                wudang["sell"].append({"price": s_price, "volume": s_vol})

            result[code_key] = {
                "code": code, "name": name, "price": price,
                "pre_close": pre_close, "change_pct": change_pct, "change": change,
                "weibi": fval(49, 0),
                "wudang": wudang,
            }
        return result

    @staticmethod
    def fetch_trend(market, code):
        if not market:
            market = auto_detect_market(code) or "sh"
        key = f"trend_{market}{code}"
        if not StockData.limiter.can_request(key, min_interval=1):
            return "rate_limited"

        secid_map = {"sh": "1", "sz": "0", "bj": "0"}
        secid = secid_map.get(market, "1")
        url = (
            f"https://push2.eastmoney.com/api/qt/stock/trends2/get"
            f"?secid={secid}.{code}"
            f"&fields1=f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13"
            f"&fields2=f51,f52,f53,f54,f55,f56,f57,f58"
            f"&iscr=0&iscca=0&ut=fa5fd1943c7b386f172d6893dbfba10b"
        )
        try:
            req = request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            print(f"获取分时数据失败 [{market}{code}]: {e}")
            return None

        trends = data.get("data", {}).get("trends", [])
        if not trends:
            return None

        parsed = []
        pre_close = data.get("data", {}).get("prePrice", 0)
        for item in trends:
            parts = item.split(",")
            if len(parts) < 8:
                continue
            try:
                parsed.append({
                    "time": parts[0].split(" ")[-1] if " " in parts[0] else parts[0],
                    "open": float(parts[1]), "high": float(parts[2]),
                    "low": float(parts[3]), "price": float(parts[4]),
                    "volume": int(parts[5]), "avg": float(parts[7]),
                })
            except (ValueError, IndexError):
                continue
        return {"pre_close": pre_close, "data": parsed}

    @staticmethod
    def fetch_kline(market, code, klt=5, lmt=120):
        if not market:
            market = auto_detect_market(code) or "sh"
        key = f"kline_{market}{code}_{klt}"
        if not StockData.limiter.can_request(key, min_interval=10):
            return "rate_limited"

        secid_map = {"sh": "1", "sz": "0", "bj": "0"}
        secid = secid_map.get(market, "1")
        url = (
            f"https://push2his.eastmoney.com/api/qt/stock/kline/get"
            f"?secid={secid}.{code}"
            f"&fields1=f1,f2,f3,f4,f5,f6"
            f"&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
            f"&klt={klt}&fqt=0&end=20500101&lmt={lmt}"
            f"&ut=fa5fd1943c7b386f172d6893dbfba10b"
        )
        try:
            req = request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            print(f"获取K线失败 [{market}{code} klt={klt}]: {e}")
            return []

        klines = data.get("data", {}).get("klines", [])
        result = []
        for item in klines:
            parts = item.split(",")
            if len(parts) < 6:
                continue
            try:
                result.append({
                    "time": parts[0], "open": float(parts[1]),
                    "close": float(parts[2]), "high": float(parts[3]),
                    "low": float(parts[4]), "volume": int(parts[5]),
                })
            except (ValueError, IndexError):
                continue
        return result


# ============ 分时图绘制 ============
class TrendChart(tk.Canvas):
    def __init__(self, parent, width=380, height=340, bg=THEME["chart_bg"]):
        super().__init__(parent, width=width, height=height, bg=bg, highlightthickness=0)
        self.width = width
        self.height = height
        self.pad_left = 45
        self.pad_right = 10
        self.pad_top = 18
        self.time_axis_h = 12
        self.price_plot_h = 140
        self.price_total_h = self.pad_top + self.price_plot_h + self.time_axis_h
        self.vol_h = 50
        self.macd_h = 70
        self.gap = 5

    def draw(self, trend_data, macd_info=None, stock_name="", fixed_time_axis=False):
        self.delete("all")
        if not trend_data or not trend_data.get("data"):
            self.create_text(
                self.width // 2, self.height // 2,
                text="暂无数据", fill=THEME["fg2"], font=("Microsoft YaHei", 12)
            )
            return

        data = trend_data["data"]
        pre_close = trend_data.get("pre_close", 0)
        if pre_close <= 0:
            pre_close = data[0]["price"] if data else 1

        prices = [d["price"] for d in data]
        avgs = [d["avg"] for d in data]
        min_p = min(prices + avgs)
        max_p = max(prices + avgs)
        max_diff = max(abs(max_p - pre_close), abs(min_p - pre_close))
        if max_diff < 0.01:
            max_diff = 0.01
        min_p = pre_close - max_diff
        max_p = pre_close + max_diff
        n = len(data)

        # 根据宽度动态调整边距（迷你图更紧凑）
        if self.width < 250:
            self.pad_left = 30
            self.pad_right = 6
        else:
            self.pad_left = 45
            self.pad_right = 10

        plot_w = self.width - self.pad_left - self.pad_right

        # 动态计算各区域高度
        available_h = self.height - self.pad_top - self.time_axis_h
        if available_h > 200:
            self.price_plot_h = int(available_h * 0.55)
            self.vol_h = int(available_h * 0.20)
            self.macd_h = int(available_h * 0.20)
            self.gap = 5
        else:
            self.price_plot_h = available_h - 5
            self.vol_h = 0
            self.macd_h = 0
            self.gap = 0

        self.price_total_h = self.pad_top + self.price_plot_h + self.time_axis_h
        vol_y0 = self.price_total_h + self.gap
        vol_y1 = vol_y0 + self.vol_h
        macd_y0 = vol_y1 + self.gap
        macd_y1 = macd_y0 + self.macd_h

        # x 坐标计算
        if fixed_time_axis:
            def parse_time(t):
                t = str(t).strip()
                if ":" in t:
                    h, m = map(int, t.split(":"))
                elif len(t) == 4:
                    h, m = int(t[:2]), int(t[2:])
                else:
                    return None
                return h, m

            def time_to_minutes(t):
                parsed = parse_time(t)
                if not parsed:
                    return 0
                h, m = parsed
                total = h * 60 + m
                if h < 12:
                    return max(0, total - 570)  # 9:30 = 0
                else:
                    return 120 + max(0, total - 780)  # 13:00 = 120

            x_positions = []
            for d in data:
                mins = time_to_minutes(d["time"])
                x_positions.append(self.pad_left + (mins / 240) * plot_w)

            def x_of(i):
                return x_positions[i]
        else:
            def x_of(i):
                return self.pad_left + (i / max(n - 1, 1)) * plot_w

        def y_price(p):
            return self.pad_top + self.price_plot_h - (p - min_p) / (max_p - min_p) * self.price_plot_h

        max_vol = max(d["volume"] for d in data) if data else 1

        def y_vol(v):
            if max_vol <= 0:
                return vol_y1
            return vol_y1 - (v / max_vol) * self.vol_h

        # 价格区
        for i in range(5):
            y = self.pad_top + self.price_plot_h * i / 4
            self.create_line(self.pad_left, y, self.width - self.pad_right, y,
                             fill=THEME["grid"], width=1)

        y_mid = y_price(pre_close)
        self.create_line(self.pad_left, y_mid, self.width - self.pad_right, y_mid,
                         fill="#252a3a", width=1, dash=(2, 2))

        for i in range(5):
            price = min_p + (max_p - min_p) * (4 - i) / 4
            y = self.pad_top + self.price_plot_h * i / 4
            color = THEME["up"] if price > pre_close else (THEME["down"] if price < pre_close else THEME["fg2"])
            self.create_text(self.pad_left - 4, y, text=f"{price:.1f}",
                             fill=color, font=("Microsoft YaHei", 8), anchor="e")

        # 横轴时间标签
        if fixed_time_axis:
            # 休市分隔线（11:30）
            noon_x = self.pad_left + (120 / 240) * plot_w
            self.create_line(noon_x, self.pad_top, noon_x, self.pad_top + self.price_plot_h,
                             fill="#2a2f3d", width=1, dash=(3, 3))

            time_labels = [("09:30", 0), ("10:30", 60), ("11:30", 120),
                           ("13:00", 120), ("14:00", 180), ("15:00", 240)]
            for label, mins in time_labels:
                tx = self.pad_left + (mins / 240) * plot_w
                self.create_text(tx, self.price_total_h - 6, text=label,
                                 fill=THEME["fg2"], font=("Microsoft YaHei", 6))
        else:
            times = [d["time"] for d in data]
            time_points = [0, n // 4, n // 2, 3 * n // 4, n - 1]
            for idx in time_points:
                if 0 <= idx < len(times):
                    t = times[idx]
                    if len(t) == 4:
                        t = f"{t[:2]}:{t[2:]}"
                    self.create_text(x_of(idx), self.price_total_h - 6, text=t,
                                     fill=THEME["fg2"], font=("Microsoft YaHei", 7))

        avg_pts = [(x_of(i), y_price(avgs[i])) for i in range(n)]
        if len(avg_pts) > 1:
            self.create_line(avg_pts, fill="#f59e0b", width=1, smooth=True)

        price_pts = [(x_of(i), y_price(prices[i])) for i in range(n)]
        if len(price_pts) > 1:
            self.create_line(price_pts, fill="#60a5fa", width=1.5, smooth=True)

        if price_pts:
            lx, ly = price_pts[-1]
            self.create_oval(lx - 3, ly - 3, lx + 3, ly + 3, fill="#60a5fa", outline="")

        title_text = stock_name
        if macd_info:
            alerts = macd_info.get("alerts", [])
            if alerts:
                title_text += "   " + " ".join(alerts)
        # 迷你图标题字号更小
        title_font = ("Microsoft YaHei", 7, "bold") if self.width < 250 else ("Microsoft YaHei", 9, "bold")
        self.create_text(self.pad_left + 4, 9, text=title_text,
                         fill=THEME["fg"], font=title_font, anchor="w")

        # 成交量区
        if self.vol_h > 0:
            self.create_line(self.pad_left, vol_y1, self.width - self.pad_right, vol_y1,
                             fill=THEME["grid"], width=1)
            bar_w = 2 if fixed_time_axis else max(1, plot_w / n * 0.65)
            for i in range(n):
                x = x_of(i)
                v = data[i]["volume"]
                y_top = y_vol(v)
                c = THEME["up"] if (i > 0 and prices[i] >= prices[i - 1]) or (i == 0 and prices[i] >= pre_close) else THEME["down"]
                self.create_rectangle(x - bar_w / 2, y_top, x + bar_w / 2, vol_y1,
                                      fill=c, outline="")
            self.create_text(self.pad_left - 4, vol_y0 + 4, text="量",
                             fill=THEME["fg2"], font=("Microsoft YaHei", 8), anchor="e")

        # MACD区
        if self.macd_h > 0:
            dif = macd_info.get("dif", []) if macd_info else []
            dea = macd_info.get("dea", []) if macd_info else []
            macd_vals = macd_info.get("macd", []) if macd_info else []

            if dif and dea and macd_vals and len(dif) == n:
                macd_min = min(macd_vals)
                macd_max = max(macd_vals)
                m_range = max(abs(macd_min), abs(macd_max), 0.0001)
                zero_y = macd_y0 + self.macd_h / 2
                self.create_line(self.pad_left, zero_y, self.width - self.pad_right, zero_y,
                                 fill="#252a3a", width=1, dash=(2, 2))

                bar_w_macd = 1 if fixed_time_axis else max(1, plot_w / n * 0.55)
                for i in range(n):
                    x = x_of(i)
                    val = macd_vals[i]
                    y = zero_y - (val / (m_range * 2)) * self.macd_h * 0.9
                    color = THEME["up"] if val >= 0 else THEME["down"]
                    self.create_line(x, zero_y, x, y, fill=color, width=max(1, int(bar_w_macd)))

                dif_pts = [(x_of(i), zero_y - (dif[i] / (m_range * 2)) * self.macd_h * 0.9) for i in range(n)]
                dea_pts = [(x_of(i), zero_y - (dea[i] / (m_range * 2)) * self.macd_h * 0.9) for i in range(n)]
                if len(dif_pts) > 1:
                    self.create_line(dif_pts, fill=THEME["fg"], width=1, smooth=True)
                if len(dea_pts) > 1:
                    self.create_line(dea_pts, fill="#fbbf24", width=1, smooth=True)

                self.create_text(self.pad_left - 4, macd_y0 + 6, text="MACD",
                                 fill=THEME["fg2"], font=("Microsoft YaHei", 8), anchor="e")
            else:
                self.create_text(self.width // 2, macd_y0 + self.macd_h // 2,
                                 text="MACD计算中...", fill="#374151", font=("Microsoft YaHei", 10))


# ============ 迷你分时折线（多股同列） ============
class MiniSparkline(tk.Canvas):
    """超紧凑迷你分时图：只有折线 + 0%线 + 下方填充"""

    def __init__(self, parent, width=140, height=28, bg=THEME["card"]):
        super().__init__(parent, width=width, height=height, bg=bg, highlightthickness=0)
        self.width = width
        self.height = height

    def draw(self, trend_data, is_up=True):
        self.delete("all")
        if not trend_data or not trend_data.get("data"):
            return

        data = trend_data["data"]
        pre_close = trend_data.get("pre_close", 0)
        if pre_close <= 0:
            pre_close = data[0]["price"] if data else 1

        prices = [d["price"] for d in data]
        min_p = min(prices)
        max_p = max(prices)
        max_diff = max(abs(max_p - pre_close), abs(min_p - pre_close))
        if max_diff < 0.01:
            max_diff = 0.01
        min_y = pre_close - max_diff
        max_y = pre_close + max_diff

        w = self.width
        h = self.height

        # 固定时间轴映射：9:30-15:00，共 240 分钟
        def time_to_minutes(t):
            t = str(t).strip()
            if ":" in t:
                h_t, m_t = map(int, t.split(":"))
            elif len(t) == 4:
                h_t, m_t = int(t[:2]), int(t[2:])
            else:
                return 0
            total = h_t * 60 + m_t
            if h_t < 12:
                return max(0, total - 570)  # 9:30 = 0
            else:
                return 120 + max(0, total - 780)  # 13:00 = 120

        pts = []
        for d in data:
            mins = time_to_minutes(d["time"])
            x = (mins / 240) * w
            y = h - (d["price"] - min_y) / (max_y - min_y) * h
            pts.append((x, y))

        if not pts:
            return

        # 0% 线（pre_close）
        zero_y = h - (pre_close - min_y) / (max_y - min_y) * h
        self.create_line(0, zero_y, w, zero_y, fill="#2a2f3d", width=1, dash=(2, 2))

        # 折线下方填充
        color = THEME["up"] if is_up else THEME["down"]
        fill_color = "#3d1f1f" if is_up else "#1f3d25"
        fill_pts = pts + [(w, h), (0, h)]
        self.create_polygon(fill_pts, fill=fill_color, outline="")

        # 折线
        if len(pts) > 1:
            self.create_line(pts, fill=color, width=1.2, smooth=True)


# ============ 五档行情面板 ============
class WudangPanel(tk.Frame):
    BIG_ORDER_THRESHOLD = 500  # 大单阈值：500手

    def __init__(self, parent, bg=THEME["card"]):
        super().__init__(parent, bg=bg, width=110)
        self.pack_propagate(False)
        tk.Label(self, text="五档", bg=bg, fg=THEME["accent"],
                 font=("Microsoft YaHei", 9, "bold")).pack(pady=(6, 2))
        self.rows = []
        # 卖盘：从上到下 卖5 ~ 卖1（价格由高到低，卖5最高，卖1最靠近中间）
        sell_labels = ["卖5", "卖4", "卖3", "卖2", "卖1"]
        for label in sell_labels:
            f = tk.Frame(self, bg=bg)
            f.pack(fill="x", padx=2, pady=1)
            tk.Label(f, text=label, bg=bg, fg=THEME["fg2"],
                     font=("Microsoft YaHei", 7), width=3, anchor="w").pack(side="left")
            sell = tk.Label(f, text="", bg=bg, fg=THEME["down"], font=("Microsoft YaHei", 8), anchor="e")
            sell.pack(side="right")
            sell_vol = tk.Label(f, text="", bg=bg, fg=THEME["fg2"], font=("Microsoft YaHei", 7), anchor="e")
            sell_vol.pack(side="right", padx=(4, 0))
            self.rows.append({"frame": f, "price": sell, "vol": sell_vol, "side": "sell"})

        tk.Frame(self, bg=THEME["border"], height=1).pack(fill="x", padx=4, pady=2)

        # 买盘：从上到下 买1 ~ 买5（价格由高到低，买1最高，最靠近中间）
        buy_labels = ["买1", "买2", "买3", "买4", "买5"]
        for label in buy_labels:
            f = tk.Frame(self, bg=bg)
            f.pack(fill="x", padx=2, pady=1)
            tk.Label(f, text=label, bg=bg, fg=THEME["fg2"],
                     font=("Microsoft YaHei", 7), width=3, anchor="w").pack(side="left")
            buy = tk.Label(f, text="", bg=bg, fg=THEME["up"], font=("Microsoft YaHei", 8), anchor="e")
            buy.pack(side="right")
            buy_vol = tk.Label(f, text="", bg=bg, fg=THEME["fg2"], font=("Microsoft YaHei", 7), anchor="e")
            buy_vol.pack(side="right", padx=(4, 0))
            self.rows.append({"frame": f, "price": buy, "vol": buy_vol, "side": "buy"})

        self.weibi_label = tk.Label(self, text="", bg=bg, fg=THEME["fg"],
                                    font=("Microsoft YaHei", 8, "bold"))
        self.weibi_label.pack(pady=(4, 6))

    def update(self, data):
        if not data:
            return
        wd = data.get("wudang", {})

        # 收集所有数量用于判断大单
        all_vols = []
        for i, r in enumerate(self.rows):
            if r["side"] == "sell":
                # sell[4]=卖5(最高) ~ sell[0]=卖1(最低)
                item = wd.get("sell", [{}] * 5)[4 - i]
            else:
                # buy[0]=买1(最高) ~ buy[4]=买5(最低)
                item = wd.get("buy", [{}] * 5)[i - 5]
            all_vols.append(item.get("volume", 0))

        max_vol = max(all_vols) if all_vols else 0

        for i, r in enumerate(self.rows):
            if r["side"] == "sell":
                # i=0(卖5) -> sell[4], i=4(卖1) -> sell[0]
                item = wd.get("sell", [{}] * 5)[4 - i]
            else:
                # i=5(买1) -> buy[0], i=9(买5) -> buy[4]
                item = wd.get("buy", [{}] * 5)[i - 5]
            price = item.get("price", 0)
            vol = item.get("volume", 0)
            r["price"].config(text=f"{price:.2f}")
            r["vol"].config(text=f"{vol}")

            # 大单高亮：>=500手 或 是该股票五档中最大量
            is_big = vol >= self.BIG_ORDER_THRESHOLD or (max_vol > 0 and vol == max_vol and vol >= 100)
            if is_big:
                r["frame"].config(bg="#3d2a0d")
                r["vol"].config(bg="#3d2a0d", fg="#fbbf24")
                r["price"].config(bg="#3d2a0d")
            else:
                r["frame"].config(bg=THEME["card"])
                r["vol"].config(bg=THEME["card"], fg=THEME["fg2"])
                r["price"].config(bg=THEME["card"])

        weibi = data.get("weibi", 0)
        color = THEME["up"] if weibi > 0 else (THEME["down"] if weibi < 0 else THEME["fg2"])
        self.weibi_label.config(text=f"委比 {weibi:+.2f}%", fg=color)


# ============ 自定义滑动条（圆角Canvas版） ============
class CustomSlider(tk.Canvas):
    def __init__(self, parent, from_=0.0, to=1.0, resolution=0.05, value=0.95,
                 command=None, bg=None, width=260, height=28):
        super().__init__(parent, width=width, height=height,
                         bg=bg or THEME["card"], highlightthickness=0)
        self.from_ = from_
        self.to = to
        self.resolution = resolution
        self._value = value
        self.command = command
        self.track_bg = "#1e2230"
        self.track_fill = "#3b82f6"
        self.knob_color = "#60a5fa"
        self.pad = 6
        self.track_h = 8
        self.knob_r = 7
        self.dragging = False
        self.bind("<Button-1>", self._on_click)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", lambda e: setattr(self, 'dragging', False))
        self.bind("<Configure>", lambda e: self.draw())
        self.draw()

    def _ratio(self, val):
        return (val - self.from_) / (self.to - self.from_)

    def _val_to_x(self, val):
        w = self.winfo_width() or 260
        return self.pad + self._ratio(val) * (w - 2 * self.pad)

    def _x_to_val(self, x):
        w = self.winfo_width() or 260
        ratio = max(0, min(1, (x - self.pad) / (w - 2 * self.pad)))
        val = self.from_ + ratio * (self.to - self.from_)
        return round(val / self.resolution) * self.resolution

    def _on_click(self, event):
        self.dragging = True
        self.set(self._x_to_val(event.x))

    def _on_drag(self, event):
        if self.dragging:
            self.set(self._x_to_val(event.x))

    def set(self, value):
        value = max(self.from_, min(self.to, round(value / self.resolution) * self.resolution))
        if value != self._value:
            self._value = value
            self.draw()
            if self.command:
                self.command(value)

    def get(self):
        return self._value

    def draw(self):
        self.delete("all")
        w = int(self.cget("width"))
        h = int(self.cget("height"))
        cy = h // 2
        tr = self.track_h // 2

        # 轨道背景（圆角）
        self._draw_round_rect(self.pad, cy - tr, w - self.pad, cy + tr,
                              tr, self.track_bg)

        # 填充部分（圆角）
        fx = self._val_to_x(self._value)
        if fx > self.pad + tr:
            self._draw_round_rect(self.pad, cy - tr, fx, cy + tr,
                                  tr, self.track_fill)
        else:
            self.create_oval(self.pad, cy - tr, self.pad + 2 * tr, cy + tr,
                             fill=self.track_fill, outline="")

        # 圆形滑块
        self.create_oval(fx - self.knob_r, cy - self.knob_r,
                         fx + self.knob_r, cy + self.knob_r,
                         fill=self.knob_color, outline=THEME["bg"], width=2)

        # 数值标签
        self.create_text(w - self.pad, cy, text=f"{self._value:.2f}",
                         fill=THEME["fg"], font=("Consolas", 9), anchor="e")

    def _draw_round_rect(self, x1, y1, x2, y2, r, fill):
        self.create_oval(x1, y1, x1 + 2 * r, y2, fill=fill, outline="")
        self.create_oval(x2 - 2 * r, y1, x2, y2, fill=fill, outline="")
        self.create_rectangle(x1 + r, y1, x2 - r, y2, fill=fill, outline="")


# ============ 辅助：Label按钮 ============
def make_lbl_btn(parent, text, bg, fg, hover_bg, command, font=None, padx=18, pady=6):
    lbl = tk.Label(parent, text=text, bg=bg, fg=fg, font=font or ("Microsoft YaHei", 9),
                   padx=padx, pady=pady, cursor="hand2")
    lbl.bind("<Button-1>", lambda e: command())
    lbl.bind("<Enter>", lambda e: lbl.config(bg=hover_bg))
    lbl.bind("<Leave>", lambda e: lbl.config(bg=bg))
    return lbl


# ============ 设置窗口（美化版） ============
class SettingsWindow:
    def __init__(self, parent, app):
        self.parent = parent
        self.app = app
        self.window = tk.Toplevel(parent)
        self.window.title("设置")
        self.window.configure(bg=THEME["bg"])
        self.window.minsize(540, 400)
        self.window.transient(parent)
        self.window.grab_set()

        sw = self.window.winfo_screenwidth()
        sh = self.window.winfo_screenheight()
        self.window.geometry(f"540x720+{(sw - 540) // 2}+{(sh - 720) // 2}")

        # 标题栏
        hdr = tk.Frame(self.window, bg=THEME["bg"], height=44)
        hdr.pack(fill="x", padx=16, pady=(10, 4))
        hdr.pack_propagate(False)
        tk.Label(hdr, text="⚙ 设置", bg=THEME["bg"], fg=THEME["fg"],
                 font=("Microsoft YaHei", 14, "bold")).pack(side="left")
        tk.Label(hdr, text="v2.0", bg=THEME["bg"], fg=THEME["fg2"],
                 font=("Microsoft YaHei", 9)).pack(side="left", padx=(6, 0))

        tk.Frame(self.window, bg=THEME["border"], height=1).pack(fill="x", padx=16)

        # 滚动容器
        scroll_wrap = tk.Frame(self.window, bg=THEME["bg"])
        scroll_wrap.pack(fill="both", expand=True, padx=16, pady=(10, 0))

        self.outer_canvas = tk.Canvas(scroll_wrap, bg=THEME["bg"], highlightthickness=0)
        scrollbar = tk.Scrollbar(scroll_wrap, orient="vertical", command=self.outer_canvas.yview,
                                  bg="#4a4a4a", activebackground="#6a6a6a",
                                  troughcolor="#0a0a0a", highlightthickness=0, bd=0, width=8)
        content = tk.Frame(self.outer_canvas, bg=THEME["bg"])

        # 延迟更新 scrollregion，避免 pack 布局未完成时 bbox 计算错误
        def _refresh_outer():
            if self.outer_canvas.winfo_exists():
                self.outer_canvas.configure(scrollregion=self.outer_canvas.bbox("all"))
        content.bind("<Configure>", lambda e: self.window.after(20, _refresh_outer))
        self.outer_canvas.create_window((0, 0), window=content, anchor="nw", width=500)
        self.outer_canvas.configure(yscrollcommand=scrollbar.set)

        self.outer_canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # 全局滚轮：根据鼠标位置决定滚动哪个Canvas
        def _on_wheel(event):
            mx, my = event.x_root, event.y_root
            delta = getattr(event, 'delta', 0)
            in_list = False
            try:
                lx1 = list_canvas.winfo_rootx()
                ly1 = list_canvas.winfo_rooty()
                lx2 = lx1 + list_canvas.winfo_width()
                ly2 = ly1 + list_canvas.winfo_height()
                in_list = lx1 <= mx <= lx2 and ly1 <= my <= ly2
            except Exception:
                pass
            target = list_canvas if in_list else self.outer_canvas
            if delta:
                target.yview_scroll(int(-1 * (delta / 120)), "units")
            elif hasattr(event, 'num'):
                target.yview_scroll(-1 if event.num == 4 else 1, "units")
            return "break"

        self.window.bind("<MouseWheel>", _on_wheel)
        self.window.bind("<Button-4>", _on_wheel)
        self.window.bind("<Button-5>", _on_wheel)

        # ===== 全局设置卡片 =====
        global_card = tk.Frame(content, bg=THEME["card"], highlightbackground=THEME["border"],
                               highlightthickness=1)
        global_card.pack(fill="x", pady=(0, 10))

        tk.Label(global_card, text="全局", bg=THEME["card"], fg=THEME["accent"],
                 font=("Microsoft YaHei", 10, "bold")).pack(anchor="w", padx=14, pady=(12, 8))

        # 透明度
        r1 = tk.Frame(global_card, bg=THEME["card"])
        r1.pack(fill="x", padx=14, pady=8)
        tk.Label(r1, text="透明度", bg=THEME["card"], fg=THEME["fg"],
                 font=("Microsoft YaHei", 9), width=10, anchor="w").pack(side="left")
        def on_alpha_change(val):
            app.root.attributes("-alpha", val)
            if app.trend_window and app.trend_window.winfo_exists():
                app.trend_window.attributes("-alpha", val)

        self.alpha_slider = CustomSlider(r1, from_=0.3, to=1.0, resolution=0.05,
                                          value=app.ui_alpha, bg=THEME["card"],
                                          command=on_alpha_change)
        self.alpha_slider.pack(side="left")

        # 字号
        r2 = tk.Frame(global_card, bg=THEME["card"])
        r2.pack(fill="x", padx=14, pady=8)
        tk.Label(r2, text="字号", bg=THEME["card"], fg=THEME["fg"],
                 font=("Microsoft YaHei", 9), width=10, anchor="w").pack(side="left")
        self.scale_var = tk.StringVar(value="中")
        if app.ui_scale <= 0.9:
            self.scale_var.set("小")
        elif app.ui_scale >= 1.1:
            self.scale_var.set("大")
        ttk.Combobox(r2, textvariable=self.scale_var, values=["小", "中", "大"],
                     width=8, state="readonly").pack(side="left")

        # 刷新间隔
        r3 = tk.Frame(global_card, bg=THEME["card"])
        r3.pack(fill="x", padx=14, pady=8)
        tk.Label(r3, text="刷新间隔", bg=THEME["card"], fg=THEME["fg"],
                 font=("Microsoft YaHei", 9), width=10, anchor="w").pack(side="left")
        self.interval_var = tk.IntVar(value=app.base_refresh_interval // 1000)
        tk.Spinbox(r3, from_=1, to=60, textvariable=self.interval_var, width=6,
                   bg=THEME["input_bg"], fg=THEME["fg"], insertbackground=THEME["fg"],
                   font=("Microsoft YaHei", 9)).pack(side="left", padx=(4, 0))
        tk.Label(r3, text="秒", bg=THEME["card"], fg=THEME["fg2"],
                 font=("Microsoft YaHei", 9)).pack(side="left")
        tk.Label(r3, text="  非交易时间自动延长至30秒", bg=THEME["card"], fg=THEME["fg2"],
                 font=("Microsoft YaHei", 8)).pack(side="left", padx=(6, 0))

        # 指标显示开关
        r4 = tk.Frame(global_card, bg=THEME["card"])
        r4.pack(fill="x", padx=14, pady=8)
        self.show_weibi_var = tk.BooleanVar(value=app.config.get("ui", {}).get("show_weibi", True))
        self.show_wudang_var = tk.BooleanVar(value=app.config.get("ui", {}).get("show_wudang", True))
        tk.Checkbutton(r4, text="主窗口显示委比", variable=self.show_weibi_var,
                       bg=THEME["card"], activebackground=THEME["card"],
                       selectcolor=THEME["accent"], fg=THEME["fg"],
                       font=("Microsoft YaHei", 9)).pack(side="left", padx=(0, 16))
        tk.Checkbutton(r4, text="分时图显示五档行情", variable=self.show_wudang_var,
                       bg=THEME["card"], activebackground=THEME["card"],
                       selectcolor=THEME["accent"], fg=THEME["fg"],
                       font=("Microsoft YaHei", 9)).pack(side="left")

        # 老板键
        r5 = tk.Frame(global_card, bg=THEME["card"])
        r5.pack(fill="x", padx=14, pady=8)
        tk.Label(r5, text="老板键", bg=THEME["card"], fg=THEME["fg"],
                 font=("Microsoft YaHei", 9), width=10, anchor="w").pack(side="left")
        self.boss_key_var = tk.StringVar(value=app.config.get("ui", {}).get("boss_key", "Ctrl+Shift+H"))
        boss_entry = tk.Entry(r5, textvariable=self.boss_key_var, width=16, font=("Microsoft YaHei", 9),
                              bg=THEME["input_bg"], fg=THEME["fg"], insertbackground=THEME["fg"],
                              bd=1, relief="solid", highlightbackground=THEME["border"])
        boss_entry.pack(side="left", padx=(4, 0))
        tk.Label(r5, text="一键隐藏/显示窗口，必须含Ctrl/Alt/Shift", bg=THEME["card"], fg=THEME["fg2"],
                 font=("Microsoft YaHei", 8)).pack(side="left", padx=(8, 0))

        # 股票数量提示
        count = len(app.config.get("stocks", []))
        hint_color = "#ef4444" if count > MAX_STOCKS_RECOMMENDED else "#22c55e"
        tk.Label(global_card,
                 text=f"当前 {count} 只股票，建议不超过 {MAX_STOCKS_RECOMMENDED} 只（避免请求过频被封IP）",
                 bg=THEME["card"], fg=hint_color, font=("Microsoft YaHei", 8)).pack(anchor="w", padx=14, pady=(0, 12))

        # ===== 自选股卡片 =====
        stocks_card = tk.Frame(content, bg=THEME["card"], highlightbackground=THEME["border"],
                               highlightthickness=1)
        stocks_card.pack(fill="x", pady=(0, 10))

        tk.Label(stocks_card, text="自选股", bg=THEME["card"], fg=THEME["accent"],
                 font=("Microsoft YaHei", 10, "bold")).pack(anchor="w", padx=14, pady=(12, 6))

        # 表头
        hdr2 = tk.Frame(stocks_card, bg=THEME["card"])
        hdr2.pack(fill="x", padx=10, pady=(2, 4))
        headers = [("市场", 6), ("代码", 10), ("名称", 8), ("背离", 6), ("周期", 14), ("", 3)]
        for text, width in headers:
            tk.Label(hdr2, text=text, bg=THEME["card"], fg=THEME["fg2"],
                     width=width, font=("Microsoft YaHei", 8)).pack(side="left", padx=(2, 0))

        # 股票列表（自适应高度，内部滚动）
        self.list_frame = tk.Frame(stocks_card, bg=THEME["card"])
        self.list_frame.pack(fill="x", padx=10, pady=2)
        self.list_frame.pack_propagate(False)

        list_canvas = tk.Canvas(self.list_frame, bg=THEME["card"], highlightthickness=0)
        list_scroll = tk.Scrollbar(self.list_frame, orient="vertical", command=list_canvas.yview,
                                   bg="#4a4a4a", activebackground="#6a6a6a",
                                   troughcolor="#0a0a0a", highlightthickness=0, bd=0, width=8)
        self.stock_list_frame = tk.Frame(list_canvas, bg=THEME["card"])

        self.stock_list_frame.bind("<Configure>", lambda e: list_canvas.configure(scrollregion=list_canvas.bbox("all")))
        list_canvas.create_window((0, 0), window=self.stock_list_frame, anchor="nw")
        list_canvas.configure(yscrollcommand=list_scroll.set)

        list_canvas.pack(side="left", fill="both", expand=True)
        # 默认隐藏滚动条，内容超出时自动显示
        self.list_scroll_visible = False
        self.list_scroll = list_scroll
        self.list_canvas = list_canvas

        def update_scrollbar(*args):
            self.window.after(50, self._update_scrollbar_visibility)

        self.stock_list_frame.bind("<Configure>", update_scrollbar)
        list_canvas.bind("<Configure>", update_scrollbar)

        self.stock_rows_ui = []
        for stock in app.config.get("stocks", []):
            self.add_stock_row(stock)
        if not self.stock_rows_ui:
            self.add_stock_row()
        self._update_list_height()

        # 添加按钮
        add_btn = make_lbl_btn(stocks_card, "+ 添加股票", THEME["btn_secondary"], THEME["fg"],
                               THEME["btn_secondary_hover"], self.add_stock_row)
        add_btn.pack(anchor="w", padx=10, pady=(6, 10))

        # ===== 底部按钮栏（固定在窗口底部） =====
        btn_bar = tk.Frame(self.window, bg=THEME["bg"], height=52)
        btn_bar.pack(fill="x", padx=16, pady=(8, 12))
        btn_bar.pack_propagate(False)

        make_lbl_btn(btn_bar, "取消", THEME["btn_secondary"], THEME["fg"],
                     THEME["btn_secondary_hover"], self.window.destroy).pack(side="right", padx=(8, 0))

        make_lbl_btn(btn_bar, "保存", THEME["btn_primary"], "#ffffff",
                     THEME["btn_primary_hover"], self.save,
                     font=("Microsoft YaHei", 10, "bold"), padx=28).pack(side="right")

    def _update_scrollbar_visibility(self):
        if not self.stock_list_frame.winfo_exists() or not self.list_canvas.winfo_exists():
            return
        list_h = self.stock_list_frame.winfo_height()
        canvas_h = self.list_canvas.winfo_height()
        needs_scroll = list_h > canvas_h
        if needs_scroll and not self.list_scroll_visible:
            self.list_scroll.pack(side="right", fill="y")
            self.list_scroll_visible = True
        elif not needs_scroll and self.list_scroll_visible:
            self.list_scroll.pack_forget()
            self.list_scroll_visible = False

    def _update_list_height(self):
        """根据股票数量动态调整列表区域高度"""
        if not hasattr(self, 'list_frame') or not self.list_frame.winfo_exists():
            return
        row_count = len(self.stock_rows_ui)
        # 表头约25 + 每行约30，最小80，最大260
        target_h = min(260, max(80, 30 + row_count * 30))
        self.list_frame.config(height=target_h)
        # 同时刷新外层滚动区域，防止添加/删除后scrollregion未更新导致无限滚动
        if hasattr(self, 'outer_canvas') and self.outer_canvas.winfo_exists():
            self.window.after(50, lambda: self.outer_canvas.configure(
                scrollregion=self.outer_canvas.bbox("all")))

    def add_stock_row(self, stock=None):
        frame = tk.Frame(self.stock_list_frame, bg=THEME["card"])
        frame.pack(fill="x", pady=2)

        div_cfg = stock.get("divergence", {}) if stock else {}
        code_var = tk.StringVar(value=stock.get("code", "") if stock else "")
        name_var = tk.StringVar(value=stock.get("name", "") if stock else "")
        div_en_var = tk.BooleanVar(value=div_cfg.get("enabled", True) if stock else True)
        periods = div_cfg.get("periods", [5, 15, 30]) if stock else [5, 15, 30]

        code_entry = tk.Entry(frame, textvariable=code_var, width=10, font=("Microsoft YaHei", 9),
                              bg=THEME["input_bg"], fg=THEME["fg"], insertbackground=THEME["fg"],
                              bd=1, relief="solid", highlightbackground=THEME["border"])
        code_entry.pack(side="left", padx=(4, 0))

        name_entry = tk.Entry(frame, textvariable=name_var, width=8, font=("Microsoft YaHei", 9),
                              bg=THEME["input_bg"], fg=THEME["fg"], insertbackground=THEME["fg"],
                              bd=1, relief="solid", highlightbackground=THEME["border"])
        name_entry.pack(side="left", padx=(4, 0))

        div_chk = tk.Checkbutton(frame, variable=div_en_var, bg=THEME["card"],
                                  activebackground=THEME["card"], selectcolor=THEME["accent"])
        div_chk.pack(side="left", padx=(8, 0))

        pf = tk.Frame(frame, bg=THEME["card"])
        pf.pack(side="left", padx=(2, 0))
        p5 = tk.BooleanVar(value=5 in periods)
        p15 = tk.BooleanVar(value=15 in periods)
        p30 = tk.BooleanVar(value=30 in periods)
        for txt, var in [("5", p5), ("15", p15), ("30", p30)]:
            tk.Checkbutton(pf, text=txt, variable=var, bg=THEME["card"], fg=THEME["fg2"],
                           activebackground=THEME["card"], selectcolor=THEME["accent"],
                           font=("Microsoft YaHei", 8)).pack(side="left", padx=(1, 0))

        del_lbl = tk.Label(frame, text="✕", bg=THEME["card"], fg="#ef4444", cursor="hand2",
                           font=("Microsoft YaHei", 10))
        del_lbl.pack(side="right", padx=(4, 0))
        del_lbl.bind("<Button-1>", lambda e, f=frame: self.remove_stock_row(f))

        self.stock_rows_ui.append({
            "frame": frame, "code": code_var, "name": name_var,
            "div_en": div_en_var, "p5": p5, "p15": p15, "p30": p30,
        })
        self._update_list_height()

    def remove_stock_row(self, frame):
        self.stock_rows_ui = [r for r in self.stock_rows_ui if r["frame"] != frame]
        frame.destroy()
        if not self.stock_rows_ui:
            self.add_stock_row()
        self._update_list_height()

    def save(self):
        stocks = []
        for r in self.stock_rows_ui:
            code = r["code"].get().strip()
            name = r["name"].get().strip()
            if not code:
                continue
            if not code.isdigit() or len(code) != 6:
                messagebox.showwarning("格式错误", f"股票代码必须是6位数字: {code}", parent=self.window)
                return
            market = auto_detect_market(code) or "sh"

            periods = []
            if r["p5"].get():
                periods.append(5)
            if r["p15"].get():
                periods.append(15)
            if r["p30"].get():
                periods.append(30)

            stocks.append({
                "code": code, "market": market, "name": name,
                "divergence": {"enabled": r["div_en"].get(), "periods": periods, "types": ["bottom", "top"]}
            })

        self.app.config["stocks"] = stocks

        alpha = self.alpha_slider.get()
        scale_map = {"小": 0.85, "中": 1.0, "大": 1.2}
        scale = scale_map.get(self.scale_var.get(), 1.0)
        interval = max(1, self.interval_var.get()) * 1000

        self.app.config.setdefault("ui", {})
        self.app.config["ui"]["alpha"] = alpha
        self.app.config["ui"]["scale"] = scale
        self.app.config["ui"]["show_weibi"] = self.show_weibi_var.get()
        self.app.config["ui"]["show_wudang"] = self.show_wudang_var.get()

        # 验证老板键格式
        boss_key = self.boss_key_var.get().strip()
        if boss_key:
            mods, vk = parse_hotkey(boss_key)
            if not mods or not vk:
                messagebox.showwarning("格式错误", "老板键格式无效，示例: Ctrl+Shift+H", parent=self.window)
                return
            self.app.config["ui"]["boss_key"] = boss_key
        else:
            self.app.config["ui"]["boss_key"] = ""
        self.app.config["window"]["refresh_interval"] = interval

        if save_config(self.app.config):
            self.app.apply_settings(alpha, scale, interval)
            self.app.reload_stocks()
            self.window.destroy()


# ============ 主程序 ============
class StockMonitorApp:
    def __init__(self, root):
        self.root = root
        self.config = load_config()
        self.stocks = self.config.get("stocks", [])
        self.base_refresh_interval = max(self.config.get("window", {}).get("refresh_interval", 3000), 1000)
        self.ui_scale = self.config.get("ui", {}).get("scale", 1.0)
        self.ui_alpha = self.config.get("ui", {}).get("alpha", 0.95)
        self.show_weibi = self.config.get("ui", {}).get("show_weibi", True)
        self.show_wudang = self.config.get("ui", {}).get("show_wudang", True)
        self.boss_key = self.config.get("ui", {}).get("boss_key", "Ctrl+Shift+H")
        self.hidden = False
        self.hotkey = GlobalHotkey()
        self._register_boss_key()
        self.hotkey.start()

        self.root.title("A股盯盘")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", self.ui_alpha)
        self.root.configure(bg=THEME["bg"])

        self.apply_scale()

        self.realtime_data = {}
        self.trend_data_cache = {}
        self.trend_macd_cache = {}
        self.trend_fetching = set()
        self.current_hover = None
        self.data_queue = queue.Queue()

        self.build_ui()
        self.setup_drag()
        self._refresh_job = None
        self.schedule_refresh()
        self.prefetch_trends()
        self.schedule_trend_refresh()
        self.process_queue()

        wx = self.config.get("window", {}).get("x", 100)
        wy = self.config.get("window", {}).get("y", 100)
        self.root.geometry(f"+{wx}+{wy}")

    def apply_scale(self):
        s = self.ui_scale
        self.font_code = tkfont.Font(family="Microsoft YaHei", size=int(11 * s), weight="bold")
        self.font_name = tkfont.Font(family="Microsoft YaHei", size=int(9 * s))
        self.font_price = tkfont.Font(family="Consolas", size=int(14 * s), weight="bold")
        self.font_pct = tkfont.Font(family="Consolas", size=int(10 * s))
        self.font_weibi = tkfont.Font(family="Consolas", size=int(8 * s))
        self.row_padx = int(6 * s)
        self.row_pady = int(3 * s)

    def apply_settings(self, alpha, scale, interval):
        self.ui_alpha = alpha
        self.ui_scale = scale
        self.base_refresh_interval = max(interval, 1000)
        self.show_weibi = self.config.get("ui", {}).get("show_weibi", True)
        self.show_wudang = self.config.get("ui", {}).get("show_wudang", True)
        self.boss_key = self.config.get("ui", {}).get("boss_key", "Ctrl+Shift+H")
        self.root.attributes("-alpha", alpha)
        self.apply_scale()
        self._register_boss_key()
        # 取消旧的刷新定时器，用新间隔重新启动
        if self._refresh_job is not None:
            self.root.after_cancel(self._refresh_job)
            self._refresh_job = None
        self.schedule_refresh()

    def _register_boss_key(self):
        self.hotkey.clear()
        mods, vk = parse_hotkey(self.boss_key)
        if mods and vk:
            self.hotkey.register(mods, vk, self.toggle_visibility)

    def toggle_visibility(self):
        self.hidden = not self.hidden
        if self.hidden:
            self.root.withdraw()
            if self.trend_window and self.trend_window.winfo_exists():
                self.trend_window.withdraw()
        else:
            self.root.deiconify()
            self.root.attributes("-topmost", True)
            # 如果当前有悬浮股票，也恢复分时图
            if self.current_hover and self.trend_window and self.trend_window.winfo_exists():
                self.trend_window.deiconify()
                self.trend_window.attributes("-topmost", True)

    def build_ui(self):
        self.list_frame = tk.Frame(self.root, bg=THEME["bg"])
        self.list_frame.pack(fill="both", expand=True, padx=self.row_padx, pady=self.row_pady)

        self.stock_rows = []
        for i, stock in enumerate(self.stocks):
            row = self.create_stock_row(stock, i == len(self.stocks) - 1)
            self.stock_rows.append(row)

        # 底部快捷添加按钮
        add_btn = tk.Label(self.list_frame, text="+", bg=THEME["card"], fg=THEME["accent"],
                           font=("Microsoft YaHei", 14, "bold"), cursor="hand2",
                           width=3, anchor="center")
        add_btn.pack(fill="x", pady=(4, 0))
        add_btn.bind("<Enter>", lambda e: add_btn.config(bg=THEME["btn_secondary_hover"]))
        add_btn.bind("<Leave>", lambda e: add_btn.config(bg=THEME["card"]))
        add_btn.bind("<Button-1>", lambda e: self.quick_add_stock())

        self.trend_window = None
        self.trend_chart = None
        self.wudang_panel = None

        self.context_menu = tk.Menu(self.root, tearoff=0, bg=THEME["bg"], fg=THEME["fg"],
                                    activebackground=THEME["card"], activeforeground=THEME["fg"])
        self.context_menu.add_command(label="设置", command=self.open_settings,
                                      font=("Microsoft YaHei", 9))
        self.context_menu.add_command(label="立即刷新", command=self.refresh_data,
                                      font=("Microsoft YaHei", 9))
        self.context_menu.add_command(label="删除", command=self.delete_context_stock,
                                      font=("Microsoft YaHei", 9))
        self.context_menu.add_separator()
        self.context_menu.add_command(label="退出", command=self.root.destroy,
                                      font=("Microsoft YaHei", 9))
        self.root.bind("<Button-3>", self.show_context_menu)

    def open_settings(self):
        SettingsWindow(self.root, self)

    def reload_stocks(self):
        self.stocks = self.config.get("stocks", [])
        self.realtime_data = {}
        self.trend_data_cache = {}
        self.trend_macd_cache = {}
        for row in self.stock_rows:
            row["frame"].destroy()
        self.stock_rows = []
        for i, stock in enumerate(self.stocks):
            r = self.create_stock_row(stock, i == len(self.stocks) - 1)
            self.stock_rows.append(r)
        self.root.update_idletasks()
        self.refresh_data()

    def show_context_menu(self, event):
        # 判断右键点击的是哪一行股票
        self._context_stock = None
        for row in self.stock_rows:
            try:
                fx1 = row["frame"].winfo_rootx()
                fy1 = row["frame"].winfo_rooty()
                fx2 = fx1 + row["frame"].winfo_width()
                fy2 = fy1 + row["frame"].winfo_height()
                if fx1 <= event.x_root <= fx2 and fy1 <= event.y_root <= fy2:
                    self._context_stock = row["stock"]
                    break
            except Exception:
                pass
        # 只有在股票行上右键时才启用删除
        if self._context_stock:
            self.context_menu.entryconfig("删除", state="normal")
        else:
            self.context_menu.entryconfig("删除", state="disabled")
        self.context_menu.post(event.x_root, event.y_root)

    def delete_context_stock(self):
        if not self._context_stock:
            return
        stock = self._context_stock
        code = stock["code"]
        name = stock.get("name", code)
        if tk.messagebox.askyesno("确认删除", f"删除 {name} ({code})？", parent=self.root):
            self.stocks = [s for s in self.stocks if s["code"] != code]
            self.config["stocks"] = self.stocks
            save_config(self.config)
            self.reload_stocks()

    def quick_add_stock(self):
        def do_add():
            code = simpledialog.askstring("添加股票", "请输入6位股票代码:", parent=self.root)
            if not code:
                return
            code = code.strip()
            if not code.isdigit() or len(code) != 6:
                messagebox.showwarning("格式错误", "股票代码必须是6位数字", parent=self.root)
                return
            market = auto_detect_market(code)
            if not market:
                messagebox.showwarning("不支持", "无法识别该股票代码的市场", parent=self.root)
                return
            # 尝试获取名称
            try:
                from urllib import request
                url = f"https://qt.gtimg.cn/q={market}{code}"
                req = request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with request.urlopen(req, timeout=5) as resp:
                    text = resp.read().decode("gbk")
                    parts = text.split("~")
                    name = parts[1] if len(parts) > 1 else ""
            except Exception:
                name = ""
            self.stocks.append({
                "code": code, "market": market, "name": name,
                "divergence": {"enabled": True, "periods": [5, 15, 30], "types": ["bottom", "top"]}
            })
            self.config["stocks"] = self.stocks
            save_config(self.config)
            self.reload_stocks()
        # 使用 after 避免在按钮回调中直接弹窗导致的事件循环问题
        self.root.after(50, do_add)

    def create_stock_row(self, stock, is_last):
        frame = tk.Frame(self.list_frame, bg=THEME["card"], highlightbackground=THEME["border"],
                         highlightthickness=1)
        frame.pack(fill="x", pady=(0, 0 if is_last else int(3 * self.ui_scale)))

        inner = tk.Frame(frame, bg=THEME["card"])
        inner.pack(fill="x", padx=int(10 * self.ui_scale), pady=int(6 * self.ui_scale))

        # 左侧：代码 + 名称
        left = tk.Frame(inner, bg=THEME["card"])
        left.pack(side="left", fill="y")

        code_label = tk.Label(left, text=stock["code"], bg=THEME["card"], fg=THEME["accent"],
                              font=self.font_code, anchor="w")
        code_label.pack(anchor="w")

        name_label = tk.Label(left, text=stock.get("name", ""), bg=THEME["card"], fg=THEME["fg2"],
                              font=self.font_name, anchor="w")
        name_label.pack(anchor="w")

        # 中间：迷你分时图
        sparkline = MiniSparkline(inner, width=140, height=26)
        sparkline.pack(side="left", fill="y", expand=True, padx=(6, 6))

        # 右侧：价格 + 涨跌幅 + 委比
        right = tk.Frame(inner, bg=THEME["card"])
        right.pack(side="right", fill="y")

        top_right = tk.Frame(right, bg=THEME["card"])
        top_right.pack(anchor="e")

        price_label = tk.Label(top_right, text="--.--", bg=THEME["card"], fg=THEME["fg"],
                               font=self.font_price, anchor="e")
        price_label.pack(side="left", padx=(0, int(6 * self.ui_scale)))

        pct_label = tk.Label(top_right, text="--.--%", bg=THEME["card"], fg=THEME["fg"],
                             font=self.font_pct, anchor="e")
        pct_label.pack(side="left")

        weibi_label = None
        if self.show_weibi:
            weibi_label = tk.Label(right, text="委比 --.--%", bg=THEME["card"], fg=THEME["fg2"],
                                   font=self.font_weibi, anchor="e")
            weibi_label.pack(anchor="e", pady=(2, 0))

        for w in [frame, inner, left, right, top_right, code_label, name_label, price_label, pct_label, sparkline]:
            w.bind("<Enter>", lambda e, s=stock: self.on_enter_stock(s))
            w.bind("<Leave>", lambda e, s=stock: self.on_leave_stock(s))
        if weibi_label:
            weibi_label.bind("<Enter>", lambda e, s=stock: self.on_enter_stock(s))
            weibi_label.bind("<Leave>", lambda e, s=stock: self.on_leave_stock(s))

        # 尝试绘制已有缓存的分时数据
        cache_key = f"{stock.get('market','')}{stock['code']}"
        if cache_key in self.trend_data_cache:
            sparkline.draw(self.trend_data_cache[cache_key], True)

        return {
            "stock": stock, "frame": frame,
            "code_label": code_label, "name_label": name_label,
            "price_label": price_label, "pct_label": pct_label,
            "weibi_label": weibi_label, "sparkline": sparkline,
        }

    def update_row(self, row, data):
        price = data.get("price", 0)
        change_pct = data.get("change_pct", 0)
        name = data.get("name", "")
        weibi = data.get("weibi", 0)

        row["price_label"].config(text=f"{price:>7.2f}")
        row["pct_label"].config(text=f"{change_pct:>+.2f}%")
        if name and not row["stock"].get("name"):
            row["name_label"].config(text=name)
            row["stock"]["name"] = name

        if change_pct > 0:
            color = THEME["up"]
            badge_bg = "#3d1f1f"
        elif change_pct < 0:
            color = THEME["down"]
            badge_bg = "#1f3d25"
        else:
            color = THEME["fg2"]
            badge_bg = THEME["card"]

        row["price_label"].config(fg=color)
        row["pct_label"].config(fg=color)
        row["frame"].config(highlightbackground=badge_bg)

        if row["weibi_label"]:
            wb_color = THEME["up"] if weibi > 0 else (THEME["down"] if weibi < 0 else THEME["fg2"])
            row["weibi_label"].config(text=f"委比 {weibi:+.2f}%", fg=wb_color)

        # 更新迷你分时图
        if row.get("sparkline"):
            cache_key = f"{row['stock'].get('market','')}{row['stock']['code']}"
            if cache_key in self.trend_data_cache:
                row["sparkline"].draw(self.trend_data_cache[cache_key], change_pct >= 0)

    def on_enter_stock(self, stock):
        self.current_hover = stock
        self.show_trend(stock)

    def on_leave_stock(self, stock):
        self.current_hover = None
        self.root.after(300, self.check_hide_trend)

    def check_hide_trend(self):
        if self.current_hover is None and self.trend_window:
            self.trend_window.destroy()
            self.trend_window = None
            self.trend_chart = None
            self.wudang_panel = None

    def show_trend(self, stock):
        if self.trend_window and self.trend_window.winfo_exists():
            self.trend_window.destroy()

        self.trend_window = tk.Toplevel(self.root)
        self.trend_window.overrideredirect(True)
        self.trend_window.attributes("-topmost", True)
        self.trend_window.attributes("-alpha", self.ui_alpha)
        self.trend_window.configure(bg=THEME["bg"])

        self.root.update_idletasks()
        x, y = self.calculate_trend_position()

        w = 510 if self.show_wudang else 400
        h = 360
        self.trend_window.geometry(f"{w}x{h}+{x}+{y}")

        # 主容器
        main_frame = tk.Frame(self.trend_window, bg=THEME["bg"])
        main_frame.pack(fill="both", expand=True, padx=5, pady=5)

        # 左侧图表
        chart_frame = tk.Frame(main_frame, bg=THEME["bg"])
        chart_frame.pack(side="left", fill="both", expand=True)

        title = f"{stock.get('market','').upper()}{stock['code']} {stock.get('name', '')}"
        self.trend_chart = TrendChart(chart_frame, width=380, height=340)
        self.trend_chart.pack()

        # 右侧五档
        if self.show_wudang:
            self.wudang_panel = WudangPanel(main_frame)
            self.wudang_panel.pack(side="right", fill="y", padx=(4, 0))
            cache_key = f"{stock.get('market','')}{stock['code']}"
            if cache_key in self.realtime_data:
                self.wudang_panel.update(self.realtime_data[cache_key])

        cache_key = f"{stock.get('market','')}{stock['code']}"
        macd_info = self.trend_macd_cache.get(cache_key, {})

        if cache_key in self.trend_data_cache:
            self.trend_chart.draw(self.trend_data_cache[cache_key], macd_info, title, fixed_time_axis=True)
        else:
            self.trend_chart.draw(None, None, title, fixed_time_axis=True)
            self.fetch_trend_async(stock)

    def calculate_trend_position(self):
        self.root.update_idletasks()
        root_x = self.root.winfo_x()
        root_y = self.root.winfo_y()
        root_w = self.root.winfo_width()

        trend_w = 510 if self.show_wudang else 400
        trend_h = 360
        gap = 8

        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()

        right_space = screen_w - (root_x + root_w + gap)
        left_space = root_x - gap

        if right_space >= trend_w:
            x = root_x + root_w + gap
        elif left_space >= trend_w:
            x = root_x - trend_w - gap
        else:
            x = screen_w - trend_w - 5

        y = root_y
        if y + trend_h > screen_h:
            y = screen_h - trend_h - 5
        if y < 0:
            y = 5

        return x, y

    def fetch_trend_async(self, stock):
        cache_key = f"{stock.get('market','')}{stock['code']}"
        if cache_key in self.trend_fetching:
            return
        self.trend_fetching.add(cache_key)

        def fetch():
            try:
                market = stock.get("market", "")
                code = stock["code"]
                if not market:
                    market = auto_detect_market(code) or "sh"

                trend = StockData.fetch_trend(market, code)
                if trend and trend != "rate_limited":
                    self.data_queue.put(("trend", cache_key, trend))

                macd_info = {"dif": [], "dea": [], "macd": [], "alerts": []}
                if trend and trend != "rate_limited" and len(trend.get("data", [])) >= 36:
                    closes_1m = [d["price"] for d in trend["data"]]
                    dif, dea, macd = calc_macd(closes_1m)
                    macd_info = {"dif": dif, "dea": dea, "macd": macd, "alerts": []}

                div_cfg = stock.get("divergence", {"enabled": True, "periods": [5, 15, 30], "types": ["bottom", "top"]})
                if div_cfg.get("enabled", True):
                    periods = div_cfg.get("periods", [5, 15, 30])
                    types = div_cfg.get("types", ["bottom", "top"])
                    alerts = []
                    for klt in periods:
                        label = f"{klt}分"
                        klines = StockData.fetch_kline(market, code, klt=klt, lmt=120)
                        if klines == "rate_limited" or len(klines) < 40:
                            continue
                        closes_k = [k["close"] for k in klines]
                        dif_k, dea_k, macd_k = calc_macd(closes_k)
                        if not dif_k:
                            continue
                        if "bottom" in types and detect_bottom_divergence(closes_k, dif_k):
                            alerts.append(f"⚡{label}底")
                        if "top" in types and detect_top_divergence(closes_k, dif_k):
                            alerts.append(f"⚠{label}顶")
                    macd_info["alerts"] = alerts

                self.data_queue.put(("macd", cache_key, macd_info))
            except Exception as e:
                print(f"获取指标失败: {e}")
            finally:
                self.trend_fetching.discard(cache_key)

        threading.Thread(target=fetch, daemon=True).start()

    def setup_drag(self):
        self.drag_start_x = 0
        self.drag_start_y = 0
        self.resize_mode = None
        self.start_w = 0
        self.start_h = 0

        def on_motion(event):
            w = self.root.winfo_width()
            h = self.root.winfo_height()
            x, y = event.x, event.y
            near_e = w - x <= 6
            near_s = h - y <= 6
            if near_e and near_s:
                self.root.config(cursor="size_nw_se")
                self.resize_mode = "se"
            elif near_e:
                self.root.config(cursor="size_we")
                self.resize_mode = "e"
            elif near_s:
                self.root.config(cursor="size_ns")
                self.resize_mode = "s"
            else:
                self.root.config(cursor="")
                self.resize_mode = None

        def on_press(event):
            if self.resize_mode:
                self.drag_start_x = event.x_root
                self.drag_start_y = event.y_root
                self.start_w = self.root.winfo_width()
                self.start_h = self.root.winfo_height()
            else:
                self.drag_start_x = event.x_root - self.root.winfo_x()
                self.drag_start_y = event.y_root - self.root.winfo_y()

        def on_drag(event):
            if self.resize_mode:
                dx = event.x_root - self.drag_start_x
                dy = event.y_root - self.drag_start_y
                new_w = self.start_w
                new_h = self.start_h
                if "e" in self.resize_mode:
                    new_w = max(280, self.start_w + dx)
                if "s" in self.resize_mode:
                    new_h = max(80, self.start_h + dy)
                self.root.geometry(f"{new_w}x{new_h}")
            else:
                x = event.x_root - self.drag_start_x
                y = event.y_root - self.drag_start_y
                self.root.geometry(f"+{x}+{y}")
                if self.trend_window and self.trend_window.winfo_exists():
                    tx, ty = self.calculate_trend_position()
                    self.trend_window.geometry(f"+{tx}+{ty}")

        self.root.bind("<Motion>", on_motion)
        self.root.bind("<Button-1>", on_press)
        self.root.bind("<B1-Motion>", on_drag)
        self.list_frame.bind("<Motion>", on_motion)
        self.list_frame.bind("<Button-1>", on_press)
        self.list_frame.bind("<B1-Motion>", on_drag)

    def get_effective_interval(self):
        if is_trading_time():
            return self.base_refresh_interval
        return max(self.base_refresh_interval, 30000)

    def schedule_refresh(self):
        self.refresh_data()
        self._refresh_job = self.root.after(self.get_effective_interval(), self.schedule_refresh)

    def schedule_trend_refresh(self):
        self.refresh_trends()
        # 固定 5 秒轮询一次（并行获取，不受股票数量影响）
        self.root.after(5000, self.schedule_trend_refresh)

    def refresh_data(self):
        def fetch():
            data = StockData.fetch_realtime(self.stocks)
            self.data_queue.put(("realtime", None, data))
        threading.Thread(target=fetch, daemon=True).start()

    def refresh_trends(self):
        def fetch_all():
            for stock in self.stocks:
                market = stock.get("market", "")
                if not market:
                    market = auto_detect_market(stock["code"]) or "sh"
                code = stock["code"]
                cache_key = f"{market}{code}"
                # 串行获取，每只间隔 0.5 秒，6 只股票 3 秒轮完
                data = StockData.fetch_trend(market, code)
                if data and data != "rate_limited":
                    self.data_queue.put(("trend", cache_key, data))
                time.sleep(0.5)
        threading.Thread(target=fetch_all, daemon=True).start()

    def prefetch_trends(self):
        """启动时立即预加载所有股票分时数据"""
        def fetch_all():
            for stock in self.stocks:
                market = stock.get("market", "")
                if not market:
                    market = auto_detect_market(stock["code"]) or "sh"
                code = stock["code"]
                cache_key = f"{market}{code}"
                # 首次启动限流器是空的，可以连续获取，间隔 0.5 秒避免服务器拒绝
                data = StockData.fetch_trend(market, code)
                if data and data != "rate_limited":
                    self.data_queue.put(("trend", cache_key, data))
                time.sleep(0.5)
        threading.Thread(target=fetch_all, daemon=True).start()

    def process_queue(self):
        try:
            while True:
                msg_type, key, data = self.data_queue.get_nowait()
                if msg_type == "realtime":
                    self.realtime_data = data
                    for row in self.stock_rows:
                        stock = row["stock"]
                        market = stock.get("market", "")
                        if not market:
                            market = auto_detect_market(stock["code"]) or "sh"
                        code_key = f"{market}{stock['code']}"
                        if code_key in data:
                            self.update_row(row, data[code_key])
                            # 如果五档面板正在显示该股票，同步更新
                            if self.wudang_panel and self.trend_window and self.trend_window.winfo_exists():
                                if self.current_hover and f"{self.current_hover.get('market','')}{self.current_hover['code']}" == code_key:
                                    self.wudang_panel.update(data[code_key])

                elif msg_type == "trend":
                    self.trend_data_cache[key] = data
                    # 更新 hover 弹窗
                    if self.current_hover and self.trend_chart and self.trend_window and self.trend_window.winfo_exists():
                        stock = self.current_hover
                        title = f"{stock.get('market','').upper()}{stock['code']} {stock.get('name', '')}"
                        macd_info = self.trend_macd_cache.get(key, {})
                        self.trend_chart.draw(data, macd_info, title, fixed_time_axis=True)
                    # 更新所有行的迷你分时图
                    for row in self.stock_rows:
                        stock = row["stock"]
                        ck = f"{stock.get('market','')}{stock['code']}"
                        if ck == key and row.get("sparkline"):
                            rt = self.realtime_data.get(ck, {})
                            is_up = rt.get("change_pct", 0) >= 0
                            row["sparkline"].draw(data, is_up)

                elif msg_type == "macd":
                    self.trend_macd_cache[key] = data
                    # 控制台调试输出
                    if data.get("alerts"):
                        print(f"[背离] {key}: {' '.join(data['alerts'])}")
                    # 更新 hover 弹窗
                    if self.current_hover and self.trend_chart and self.trend_window and self.trend_window.winfo_exists():
                        stock = self.current_hover
                        title = f"{stock.get('market','').upper()}{stock['code']} {stock.get('name', '')}"
                        trend = self.trend_data_cache.get(key)
                        self.trend_chart.draw(trend, data, title, fixed_time_axis=True)
        except queue.Empty:
            pass
        self.root.after(200, self.process_queue)


def main():
    root = tk.Tk()
    StockMonitorApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
