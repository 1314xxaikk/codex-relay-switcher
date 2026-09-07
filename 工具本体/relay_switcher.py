# -*- coding: utf-8 -*-
"""
Codex 中转站切换助手 (codex-relay-switcher)
Windows 桌面问卷式小工具：一键切换 Codex 连的 AI 中转站。
- 问卷式表单：中转站地址 / API Key / 连接方式 / 模型
- 方案管理：可存任意多套(方案一、方案二...)，改名、删除、一键切换、一键恢复
- 说明：程序写 C 盘配置文件前会自动备份(.bak_switcher)，内置一键恢复
运行：本机双击 启动中转站切换助手.bat
"""
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext

# ---------------- 路径与配置 ----------------
# 通用默认值：不写死任何人的机器。
# 想改目标路径/代理，请在本文件同目录建 paths.json 覆盖（该文件已在 .gitignore，不会上传）。
APP_DIR = os.path.dirname(os.path.abspath(__file__))


def _user_codex_config():
    return os.path.join(os.path.expanduser("~"), ".codex", "config.toml")


DEFAULT_PATHS = {
    "codex_config": "",   # 空 => 自动用 ~/.codex/config.toml
    "relay_dir": "",      # 本地中继目录（可选；仅“本地中继模式”需要）
    "relay_py": "",       # 本地中继脚本 .py 完整路径
    "key_txt": "",        # 中继目录里的 key.txt（可选）
    "pythonw": "",        # 空 => 自动找 pythonw / python
    "proxy_url": "",      # 默认 HTTP 代理地址（可为空）
    "model_catalog": "",  # Codex 模型清单文件路径(空则从 config.toml 的 model_catalog_json 自动找)
    "relay_port": 8123,   # 本地中继监听端口
    "data_dir": "",       # 空 => 程序同目录下的 _data
}


def load_paths():
    p = dict(DEFAULT_PATHS)
    pj = os.path.join(APP_DIR, "paths.json")
    if os.path.exists(pj):
        try:
            with open(pj, "r", encoding="utf-8") as f:
                p.update(json.load(f))
        except Exception:
            pass
    import shutil
    if not p.get("codex_config"):
        p["codex_config"] = _user_codex_config()
    if not p.get("data_dir"):
        p["data_dir"] = os.path.join(APP_DIR, "_data")
    if p.get("relay_py") and not p.get("relay_dir"):
        p["relay_dir"] = os.path.dirname(p["relay_py"])
    if p.get("relay_dir") and not p.get("key_txt"):
        p["key_txt"] = os.path.join(p["relay_dir"], "key.txt")
    if not p.get("pythonw"):
        p["pythonw"] = shutil.which("pythonw") or ""
    return p

P = load_paths()
DATA_DIR = P["data_dir"]
PROFILES_FILE = os.path.join(DATA_DIR, "profiles.json")
STATE_FILE = os.path.join(DATA_DIR, "last_backup.json")
DEFAULT_PROFILE_NAME = "方案一_当前配置"
LOG_FILE_PATH = os.path.join(DATA_DIR, "操作日志.txt")


def write_log(txt):
    """把一行/多行写入操作日志文件（带时间戳）。"""
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(LOG_FILE_PATH, "a", encoding="utf-8") as f:
            for ln in str(txt).splitlines():
                f.write("[%s] %s\n" % (ts, ln))
    except Exception:
        pass


# 内置近一年主流模型（下拉选择用；点“获取该站真实模型”可拉取该站真实列表合并进来）
BUILTIN_MODELS = [
    "claude-opus-4-8", "claude-opus-5", "claude-sonnet-4-5",
    "gpt-5.6-sol", "gpt-5.2", "gpt-5", "gpt-4.1",
    "deepseek-v4-flash", "deepseek-v3.2", "deepseek-r1",
    "qwen3-max", "qwen3-coder", "qwen2.5-coder-32b",
    "gemini-2.5-pro", "gemini-2.5-flash",
    "glm-4.6", "glm-4.5", "kimi-k2", "moonshot-v1-128k",
    "llama-3.3-70b", "mistral-large-2",
]

# ---------------- 通用小工具 ----------------

def now_stamp():
    return time.strftime("%Y%m%d_%H%M%S")


def ensure_data_dir():
    os.makedirs(DATA_DIR, exist_ok=True)


def read_text(p):
    with open(p, "r", encoding="utf-8-sig") as f:
        return f.read()


def write_text(p, s):
    with open(p, "w", encoding="utf-8") as f:
        f.write(s)


def backup_file(path):
    """复制一份 .bak_switcher_时间戳 备份，返回备份路径。"""
    if not os.path.exists(path):
        return None
    b = path + ".bak_switcher_" + now_stamp()
    shutil.copy2(path, b)
    return b


def is_port_open(port, host="127.0.0.1", timeout=0.5):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


# ---------------- Codex config.toml 修改 ----------------

def _quote(v):
    return json.dumps(str(v), ensure_ascii=False)


def toml_set_keys(text, top_keys, section_key):
    """对 toml 文本做行级替换。top_keys: {键:值}; section_key: [xxx] 下第一个出现的键:值。
    找不到的键返回 not_found 列表。"""
    lines = text.splitlines(keepends=True)
    not_found = []
    in_section = False
    for i, ln in enumerate(lines):
        s = ln.strip()
        if s.startswith("["):
            in_section = (s.rstrip("]").strip() == section_key) if section_key else False
        if in_section and section_key and ln.strip().startswith("base_url"):
            lines[i] = "base_url = " + _quote(section_key["base_url"]) + ("\n" if ln.endswith("\n") else "")
            section_key["_done"] = True
    # 处理顶层键
    done = set()
    in_any_section = False
    for i, ln in enumerate(lines):
        s = ln.strip()
        if s.startswith("["):
            in_any_section = True
        elif not in_any_section and not s.startswith("#") and s and "=" in s and not s.startswith(("[", "]")):
            key = s.split("=", 1)[0].strip()
            if key in top_keys and key not in done:
                lines[i] = key + " = " + _quote(top_keys[key]) + ("\n" if ln.endswith("\n") else "")
                done.add(key)
    new_text = "".join(lines)
    for k in top_keys:
        if k not in done:
            not_found.append("顶层:" + k)
    if section_key and not section_key.get("_done"):
        not_found.append("段:" + section_key["_key"])
    return new_text, not_found


def set_codex_config(model, base_url):
    """改 ~/.codex/config.toml：顶层 model、openai_base_url 和
    [model_providers.openai-chat-completions] 下的 base_url。"""
    p = P["codex_config"]
    if not os.path.exists(p):
        return False, "找不到 Codex 配置: " + p
    b = backup_file(p)
    text = read_text(p)
    sec = {"_key": "model_providers.openai-chat-completions", "base_url": base_url}
    new_text, nf = toml_set_keys(text, {"model": model, "openai_base_url": base_url}, None)
    # 处理段内 base_url
    lines = new_text.splitlines(keepends=True)
    in_sec = False
    sec_done = False
    for i, ln in enumerate(lines):
        s = ln.strip()
        if s.startswith("["):
            in_sec = (s.rstrip("]").strip() == "model_providers.openai-chat-completions")
        elif in_sec and not sec_done and s.startswith("base_url"):
            lines[i] = "base_url = " + _quote(base_url) + ("\n" if ln.endswith("\n") else "")
            sec_done = True
    if not sec_done:
        nf.append("段:base_url")
    write_text(p, "".join(lines))
    return True, b


# ---------------- 本地中继(可选) 修改 ----------------

def relay_current():
    """读出中继脚本当前的 上游/代理/模型覆盖，以及当前真正生效的 Key。
    Key 读取顺序和中继脚本一致：环境变量 AGENT_ROUTER_TOKEN → 注册表(HKCU) → key.txt。"""
    out = {"upstream": "", "proxy": "", "model": "", "key": ""}
    rp = P["relay_py"]
    if os.path.exists(rp):
        t = read_text(rp)
        m = re.search(r'^UPSTREAM\s*=\s*"([^"]*)"', t, re.M)
        if m:
            out["upstream"] = m.group(1)
        m = re.search(r'^HEMA_PROXY\s*=\s*"([^"]*)"', t, re.M)
        if m:
            out["proxy"] = m.group(1)
        m = re.search(r'^MODEL_OVERRIDE\s*=.*?,\s*"([^"]*)"\s*\)', t, re.M)
        if m:
            out["model"] = m.group(1)
    # 读 Key：环境变量 → 注册表 → key.txt
    k = os.environ.get("AGENT_ROUTER_TOKEN", "").strip()
    if not k:
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as rk:
                v = winreg.QueryValueEx(rk, "AGENT_ROUTER_TOKEN")[0]
                if isinstance(v, str):
                    k = v.strip()
        except Exception:
            pass
    if not k:
        kt = P["key_txt"]
        if os.path.exists(kt):
            try:
                k = read_text(kt).strip()
            except Exception:
                pass
    out["key"] = k
    return out

def patch_relay(upstream, proxy, model_override):
    """改写中继脚本三处常量：UPSTREAM / HEMA_PROXY / MODEL_OVERRIDE。"""
    rp = P["relay_py"]
    if not os.path.exists(rp):
        return False, "找不到中继脚本: " + rp
    b = backup_file(rp)
    t = read_text(rp)
    n = 0
    new_lines = []
    for ln in t.splitlines(keepends=True):
        s = ln.strip()
        if s.startswith("UPSTREAM ="):
            new_lines.append('UPSTREAM = ' + _quote(upstream) + ("\n" if ln.endswith("\n") else ""))
            n += 1
        elif s.startswith("HEMA_PROXY ="):
            new_lines.append('HEMA_PROXY = ' + _quote(proxy) + ("\n" if ln.endswith("\n") else ""))
            n += 1
        elif s.startswith("MODEL_OVERRIDE ="):
            new_lines.append('MODEL_OVERRIDE = os.environ.get("AGENT_ROUTER_MODEL", ' + _quote(model_override) + ")\n")
            n += 1
        else:
            new_lines.append(ln)
    if n < 3:
        return False, "中继脚本里没找全要改的三行(只找到%d行)，已中止，未改动。" % n
    out = "".join(new_lines)
    # 关键修复：代理为空(直连)时不能套空代理处理器，否则报 no host given
    if "if HEMA_PROXY:" not in out:
        bug = """        opener = urllib.request.build_opener(urllib.request.ProxyHandler({
            "http": HEMA_PROXY, "https": HEMA_PROXY,
        }))"""
        fixed = """        if HEMA_PROXY:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({
                "http": HEMA_PROXY, "https": HEMA_PROXY,
            }))
        else:
            opener = urllib.request.build_opener()"""
        out = out.replace(bug, fixed, 1)
    write_text(rp, out)
    return True, b


def write_key(key):
    """写入 key.txt(若有配置，覆盖前自动备份) + 用户环境变量 AGENT_ROUTER_TOKEN(注册表+广播)。"""
    kt = (P.get("key_txt") or "").strip()
    bak = None
    if kt:
        try:
            os.makedirs(os.path.dirname(kt), exist_ok=True)
            if os.path.exists(kt):
                bak = backup_file(kt)
            write_text(kt, key.strip())
        except Exception as ex:
            return False, "写 key.txt 失败: " + str(ex)
    try:
        import winreg
        import ctypes
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, "AGENT_ROUTER_TOKEN", 0, winreg.REG_SZ, key.strip())
        HWND_BROADCAST = 0xFFFF
        WM_SETTINGCHANGE = 0x1A
        ctypes.windll.user32.SendMessageTimeoutW(HWND_BROADCAST, WM_SETTINGCHANGE, 0, "Environment", 2, 5000, None)
        os.environ["AGENT_ROUTER_TOKEN"] = key.strip()
    except Exception as ex:
        return False, "写环境变量失败: " + str(ex)
    extra = "（key.txt 已建）" if kt and not bak else ("（key.txt 已备份+写入）" if kt and bak else "（无 key.txt 配置，仅写环境变量）")
    return True, bak if bak else ("环境变量 AGENT_ROUTER_TOKEN 已写 " + extra)

def _pid_on_port(port):
    if os.name != "nt":
        return None
    try:
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command",
             "(Get-NetTCPConnection -LocalPort %d -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1).OwningProcess" % port],
            stderr=subprocess.DEVNULL).decode().strip()
        return int(out) if out.isdigit() else None
    except Exception:
        return None


def stop_relay(port):
    pid = _pid_on_port(port)
    if pid:
        try:
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, timeout=10)
        except Exception:
            pass


def start_relay():
    pw = P["pythonw"]
    rp = P["relay_py"]
    rd = P["relay_dir"]
    if not os.path.exists(rp):
        return False, "中继脚本不存在: " + rp
    exe = pw if os.path.exists(pw) else sys.executable
    try:
        subprocess.Popen([exe, rp], cwd=rd, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception as e:
        return False, "启动中继失败: " + str(e)
    return True, "已拉起中继"


def restart_relay(port):
    stop_relay(port)
    time.sleep(1.0)
    return start_relay()


# ---------------- 方案(档案)存储 ----------------

def load_profiles():
    ensure_data_dir()
    if not os.path.exists(PROFILES_FILE):
        return {}
    try:
        with open(PROFILES_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def save_profiles(d):
    ensure_data_dir()
    tmp = PROFILES_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.replace(tmp, PROFILES_FILE)


def ensure_default_profile():
    """自动把“当前配置”抓成/同步成 方案一（仅本地保存，不上传）。"""
    d = load_profiles()
    c = relay_current()
    key = (c["key"] or "").strip()
    if DEFAULT_PROFILE_NAME in d:
        p = d[DEFAULT_PROFILE_NAME]
        changed = False
        if not (p.get("api_key") or "").strip() and key:
            p["api_key"] = key
            changed = True
        if "proxy" not in p:
            p["proxy"] = c["proxy"] or (P.get("proxy_url") or "")
            changed = True
        if p.get("model") and not isinstance(p.get("models"), list):
            p["models"] = [p["model"]]
            changed = True
        if changed:
            save_profiles(d)
        return d
    if d:
        return d
    # 仅在确实读得到本地中继脚本时才自动抓，否则不建中继方案
    if c["upstream"]:
        model = c["model"] or (BUILTIN_MODELS[0] if BUILTIN_MODELS else "")
        d = {
            DEFAULT_PROFILE_NAME: {
                "mode": "relay",
                "address": c["upstream"],
                "api_key": key,
                "model": model,
                "models": [model] if model else [],
                "network": "proxy" if c["proxy"] else "direct",
                "proxy": c["proxy"] or "",
                "note": "自动抓取的当前配置",
            }
        }
        save_profiles(d)
    return d

def discover_model_catalog_path():
    """找 Codex 的模型清单文件：先看 paths.json 的 model_catalog，再从 config.toml 的 model_catalog_json 读。"""
    if (P.get("model_catalog") or "").strip():
        return P["model_catalog"].strip()
    cfg = P.get("codex_config") or ""
    if os.path.exists(cfg):
        try:
            t = read_text(cfg)
            m = re.search(r'^\s*model_catalog_json\s*=\s*"([^"]+)"', t, re.M)
            if m:
                p = m.group(1).strip()
                if p.startswith("file://"):
                    p = p[len("file://"):]
                return p
        except Exception:
            pass
    return ""


def sync_model_catalog(model_ids):
    """把模型名合并进 Codex 的模型清单文件，让 Codex 顶部能显示这些模型并可切换。
    只增不改：已有的模型条目原样保留。"""
    ids = []
    for m in model_ids:
        m = str(m or "").strip()
        if m and m not in ids:
            ids.append(m)
    if not ids:
        return False, "没有要同步的模型。"
    cat_path = discover_model_catalog_path()
    if not cat_path:
        return False, "找不到 Codex 模型清单(model_catalog_json)。可在 paths.json 里写 model_catalog 路径。"
    try:
        data = {}
        if os.path.exists(cat_path):
            data = json.loads(read_text(cat_path))
        models = data.get("models")
        if not isinstance(models, list):
            models = []
        data["models"] = models
        by_slug = {}
        for mm in models:
            if isinstance(mm, dict) and mm.get("slug"):
                by_slug[str(mm["slug"])] = mm
        # 用一条已有条目当模板，保证字段齐全、Codex 认得
        tmpl = {}
        for mm in models:
            if isinstance(mm, dict) and mm.get("slug"):
                tmpl = dict(mm)
                break
        added = 0
        for mid in ids:
            if mid in by_slug:
                continue
            ent = dict(tmpl) if tmpl else {}
            ent.update({"slug": mid, "display_name": mid, "description": ""})
            ent.setdefault("provider", "openai-chat-completions")
            ent.setdefault("hidden", False)
            ent.setdefault("visibility", "list")
            models.append(ent)
            by_slug[mid] = ent
            added += 1
        if added:
            bak = backup_file(cat_path) if os.path.exists(cat_path) else None
            tmp = cat_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, cat_path)
            extra = ("[已备份 " + os.path.basename(bak) + "]" if bak else "")
            return True, "已把 %d 个模型加进 Codex 模型清单：%s %s" % (added, cat_path, extra)
        return True, "这些模型在 Codex 清单里已存在，无需重复添加。"
    except Exception as ex:
        return False, "写模型清单失败: " + str(ex)

def http_get_json(url, api_key, proxy=None, extra=None, timeout=12):
    import urllib.request
    import urllib.error
    hdrs = {"User-Agent": "relay-switcher", "Accept": "application/json"}
    if api_key:
        hdrs["Authorization"] = "Bearer " + api_key.strip()
    if extra:
        hdrs.update(extra)
    if proxy:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    else:
        opener = urllib.request.build_opener()
    req = urllib.request.Request(url, headers=hdrs)
    with opener.open(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def norm_models_url(address):
    a = address.strip().rstrip("/")
    if a.lower().endswith("/v1"):
        return a + "/models"
    return a + "/v1/models"


SPOOF_HEADERS = {
    "anthropic-version": "2023-06-01",
    "anthropic-dangerous-direct-browser-access": "true",
    "anthropic-beta": "claude-code-20250219,interleaved-thinking-2025-05-14,prompt-caching-scope-2026-01-21",
    "x-app": "cli",
    "User-Agent": "claude-cli/2.1.31 (external, cli)",
    "x-stainless-lang": "js",
    "x-stainless-package-version": "0.80.0",
    "x-stainless-os": "Linux",
    "x-stainless-arch": "x64",
    "x-stainless-runtime": "node",
    "x-stainless-runtime-version": "v20.18.0",
}


def probe(address, api_key, proxy=None, timeout=8):
    """依次尝试：直连 → 经代理 → 直连+伪装头 → 经代理+伪装头。
    返回 (ok, 方式文字, models)。"""
    url = norm_models_url(address)
    proxy = proxy or None
    if proxy:
        seq = [(proxy, None), (proxy, SPOOF_HEADERS)]
    else:
        seq = [(None, None), (None, SPOOF_HEADERS)]
    last_err = ""
    for p, extra in seq:
        label = "直连" if (not p and not extra) else ("经代理" if (p and not extra) else ("直连+伪装" if (extra and not p) else "经代理+伪装"))
        try:
            data = http_get_json(url, api_key, proxy=p, extra=extra, timeout=timeout)
            models = []
            if isinstance(data, dict):
                mm = data.get("data") or data.get("models") or []
                for x in mm:
                    if isinstance(x, str):
                        models.append(x)
                    elif isinstance(x, dict) and x.get("id"):
                        models.append(str(x["id"]))
            if models:
                return True, label, models
            last_err = "站点返回了空模型列表"
        except Exception as ex:
            last_err = str(ex)
    return False, last_err, []


def test_connectivity(address, api_key, proxy_url):
    if not address.strip():
        return "请先填中转站地址"
    # 先快速试直连
    okD, howD, modelsD = probe(address, api_key, proxy=None)
    if okD and howD == "直连":
        return "直连成功 ✅（该站返回 %d 个模型）。网络方式可选手「直连」。" % len(modelsD)
    # 再试代理
    okP, howP, modelsP = probe(address, api_key, proxy=proxy_url)
    if okP:
        if "伪装" in howP:
            return "该站需要伪装头，走代理才通（%s，返回 %d 个模型）。请用「本机中继模式」连接。" % (howP, len(modelsP))
        return "直连不通，走代理成功 ✅（返回 %d 个模型）。网络方式建议选「走本地代理」。" % len(modelsP)
    if okD:
        return "该站要伪装头且直连可达（%s，返回 %d 个模型）。请用「本机中继模式」连接。" % (howD, len(modelsD))
    return "测不通：直连与走代理都失败。\n建议：确认地址/Key 正确，或用「本机中继模式」。"

def health_one(address, api_key, proxy=None, timeout=8):
    """测单个方案：连通性 + 方式 + 延迟 + 模型数。返回 dict。"""
    url = norm_models_url(address)
    proxy = proxy or None
    seq = [(proxy, None), (proxy, SPOOF_HEADERS)] if proxy else [(None, None), (None, SPOOF_HEADERS)]
    last_err = ""
    for p, extra in seq:
        how = "经代理" if p else "直连"
        if extra:
            how += "+伪装"
        t0 = time.perf_counter()
        try:
            data = http_get_json(url, api_key, proxy=p, extra=extra, timeout=timeout)
            sec = round(time.perf_counter() - t0, 2)
            models = []
            if isinstance(data, dict):
                mm = data.get("data") or data.get("models") or []
                for x in mm:
                    if isinstance(x, str):
                        models.append(x)
                    elif isinstance(x, dict) and x.get("id"):
                        models.append(str(x["id"]))
            if models:
                return {"ok": True, "how": how, "sec": sec, "models": len(models), "err": ""}
            last_err = "空模型列表"
        except Exception as ex:
            last_err = str(ex)
    return {"ok": False, "how": "", "sec": 0, "models": 0, "err": last_err}


def _post_json(url, api_key, payload, proxy=None, extra=None, timeout=30):
    import urllib.request
    import urllib.error
    hdrs = {"Content-Type": "application/json", "Accept": "application/json", "User-Agent": "relay-switcher"}
    if api_key:
        hdrs["Authorization"] = "Bearer " + api_key.strip()
    if extra:
        hdrs.update(extra)
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=hdrs, method="POST")
    if proxy:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    else:
        opener = urllib.request.build_opener()
    try:
        with opener.open(req, timeout=timeout) as resp:
            return True, resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        bd = e.read().decode("utf-8", "replace")[:300]
        return False, e.code, "HTTP %s: %s" % (e.code, bd)
    except Exception as ex:
        return False, 0, str(ex)


def trial(address, api_key, model, proxy=None, timeout=30):
    """试通：用 Key+模型真实发一条最小请求，确认真的能跑（Codex 走 /v1/responses）。"""
    base = address.strip().rstrip("/")
    if not base:
        return "请先填中转站地址"
    ep = (base + "/responses") if base.lower().endswith("/v1") else (base + "/v1/responses")
    proxy = proxy or None
    seq = [(proxy, None), (proxy, SPOOF_HEADERS)] if proxy else [(None, None), (None, SPOOF_HEADERS)]
    last = ""
    for p, extra in seq:
        how = "经代理" if p else "直连"
        if extra:
            how += "+伪装"
        t0 = time.perf_counter()
        ok, code, text = _post_json(ep, api_key, {"model": model, "input": "ping", "stream": False},
                                    proxy=p, extra=extra, timeout=timeout)
        sec = round(time.perf_counter() - t0, 2)
        if ok:
            snippet = text[:120].replace("\n", " ")
            return "试通成功 ✅（%s，%.1f 秒，模型 %s）\n返回：%s…" % (how, sec, model, snippet)
        last = text if isinstance(text, str) else str(text)
    return "试通失败 ❌（模型 %s）\n最后尝试：%s\n可能原因：Key 不对 / 模型名不支持 / 该站不支持 /v1/responses。" % (model, last)


def scan_token_usage(days=7):
    """扫 Codex 本地会话日志里的 token_count，汇总用量。返回 (文本, 数字dict)。"""
    base_dir = os.path.join(os.path.expanduser("~"), ".codex", "sessions")
    now = time.time()
    cutoff = now - days * 86400
    total = 0
    day24 = 0
    files = 0
    evts = 0
    if os.path.isdir(base_dir):
        for root, dirs, names in os.walk(base_dir):
            for nm in names:
                if not nm.endswith(".jsonl"):
                    continue
                files += 1
                fp = os.path.join(root, nm)
                try:
                    with open(fp, "r", encoding="utf-8", errors="ignore") as f:
                        for line in f:
                            if "token_count" not in line:
                                continue
                            try:
                                obj = json.loads(line)
                            except Exception:
                                continue
                            pl = obj.get("payload") or {}
                            if obj.get("type") != "token_count" and pl.get("type") != "token_count":
                                continue
                            ts = pl.get("timestamp") or obj.get("timestamp", "")
                            try:
                                st = time.mktime(time.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S"))
                            except Exception:
                                st = now
                            info = pl.get("info") or {}
                            lu = info.get("last_token_usage") or info.get("total_token_usage") or {}
                            tt = lu.get("total_tokens") or 0
                            if not tt and isinstance(lu, dict):
                                tt = (lu.get("input_tokens") or 0) + (lu.get("output_tokens") or 0)
                            if tt:
                                evts += 1
                                total += int(tt)
                                if st >= now - 86400:
                                    day24 += int(tt)
                except Exception:
                    continue
    txt = []
    txt.append("统计范围：本地 Codex 会话日志（最近 %d 天）" % days)
    txt.append("已扫描会话文件：%d 个" % files)
    txt.append("token 事件：%d 条" % evts)
    txt.append("近 24 小时：%s tokens（估算）" % format(int(day24), ","))
    txt.append("近 7 天合计：%s tokens（估算）" % format(int(total), ","))
    return "\n".join(txt), {"24h": int(day24), "7d": int(total), "files": files, "events": evts}


def restart_codex():
    """重启 Codex 桌面应用（先关掉再打开）。"""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "$ps = Get-Process ChatGPT -ErrorAction SilentlyContinue | Where-Object { $_.Path -like '*OpenAI.Codex*' };"
             "$path = ($ps | Select-Object -First 1).Path;"
             "$ids = ($ps | Select-Object -Expand Id) -join ',';"
             "Write-Output ('PATH:' + $path); Write-Output ('IDS:' + $ids)"],
            capture_output=True, text=True, timeout=20).stdout
    except Exception as e:
        return False, "查询进程失败: " + str(e)
    path = ""
    ids = []
    for ln in (out or "").splitlines():
        if ln.startswith("PATH:"):
            path = ln[5:].strip()
        if ln.startswith("IDS:"):
            ids = [x for x in ln[4:].strip().split(",") if x]
    if not ids:
        return False, "没找到正在运行的 Codex 应用（ChatGPT.exe）"
    try:
        for pid in ids:
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, timeout=10)
    except Exception as e:
        return False, "关闭 Codex 失败: " + str(e)
    time.sleep(2.0)
    if not path:
        return False, "Codex 已关闭，但没找到它的启动路径，无法自动打开。"
    try:
        subprocess.Popen([path])
    except Exception as e:
        return False, "Codex 已关闭，重新打开失败: " + str(e)
    return True, "Codex 已重启（%d 个进程）" % len(ids)

def apply_profile(prof, log):
    """执行切换。prof: {mode,address,api_key,model,network}。返回 (ok, 报告文字)。"""
    steps = []
    errors = []
    def step(ok, txt):
        steps.append(("✅ " if ok else "❌ ") + txt)
        if not ok:
            errors.append(txt)
    # 备份记录文件
    ensure_data_dir()
    backup_state = {}

    key = (prof.get("api_key") or "").strip()
    if not key:
        return False, "API Key 是空的，先填 key。"

    mode = prof.get("mode", "relay")
    model = (prof.get("model") or "").strip() or "deepseek-v4-flash"
    address = (prof.get("address") or "").strip()
    if not address:
        return False, "中转站地址是空的。"

    # 1) 写 key（两处：key.txt + 环境变量 AGENT_ROUTER_TOKEN）
    ok, msg = write_key(key)
    step(ok, "写入 API Key → " + msg)
    backup_state["key"] = msg if ok and not msg.startswith("写") else None

    if mode == "relay":
        use_proxy = prof.get("network") == "proxy"
        proxy = (prof.get("proxy") or "").strip() if use_proxy else ""
        if use_proxy and not proxy:
            proxy = P.get("proxy_url") or ""
        # 改 Codex 配置指向本地中继
        ok, msg = set_codex_config(model, "http://127.0.0.1:%d/v1" % P["relay_port"])
        if ok:
            step(True, "Codex 配置已指向本地中继 8123（模型=" + model + "）[备份 " + os.path.basename(msg) + "]")
            backup_state["codex"] = msg
        else:
            step(False, msg)
        # 改中继脚本
        ok, msg = patch_relay(address, proxy, model)
        if ok:
            step(True, "中继上游已改为 " + address + "（代理=" + (proxy or "直连") + "）[备份 " + os.path.basename(msg) + "]")
            backup_state["relay"] = msg
        else:
            step(False, msg)
        # 重启中继
        ok, msg = restart_relay(P["relay_port"])
        step(ok, "重启本地中继(8123) → " + msg)
        time.sleep(1.5)
        ok = is_port_open(P["relay_port"])
        step(ok, "中继 8123 端口 " + ("已就绪 ✅" if ok else "未起来 ⚠️"))
        step(True, "注意：如 Codex 正在运行，需重启 Codex 或新开对话才会用上新 Key/新配置。")
    else:
        # 直连模式
        ok, msg = set_codex_config(model, address)
        if ok:
            step(True, "Codex 已改为直连 " + address + "（模型=" + model + "）[备份 " + os.path.basename(msg) + "]")
            backup_state["codex"] = msg
        else:
            step(False, msg)
        step(True, "提醒：直连模式下请求不再经过本地中继；若该站要代理才能连，请改用「本机中继模式」。")

    # 2) 把方案里的模型同步进 Codex 模型下拉（Codex 里才能显示/切换）
    prof_models = prof.get("models")
    model_ids = [m for m in prof_models if m] if isinstance(prof_models, list) else ([model] if model else [])
    okc, msgc = sync_model_catalog(model_ids)
    steps.append(("✅ " if okc else "⚠️ ") + "Codex 模型下拉同步 → " + msgc)

    # 保存本次备份位置，供“恢复上次”
    if backup_state:
        try:
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(backup_state, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
    return (not errors), "\n".join(steps)


def restore_last(log=None):
    """从备份恢复上次切换前的 config.toml / 中继脚本 / key，并重启中继。"""
    if not os.path.exists(STATE_FILE):
        return False, "没有找到上次备份记录，无法恢复。"
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        st = json.load(f)
    msgs = []
    ok_all = True
    for what, bpath in st.items():
        if not bpath or not os.path.exists(bpath):
            continue
        target = None
        if what == "codex":
            target = P["codex_config"]
        elif what == "relay":
            target = P["relay_py"]
        elif what == "key":
            target = P["key_txt"]
        if target:
            try:
                shutil.copy2(bpath, target)
                msgs.append("已恢复 " + what + " ← " + os.path.basename(bpath))
            except Exception as e:
                ok_all = False
                msgs.append("恢复 " + what + " 失败: " + str(e))
    if st.get("relay"):
        ok, m = restart_relay(P["relay_port"])
        msgs.append("重启中继 → " + m)
    return ok_all, "\n".join(msgs) if msgs else "没有可恢复的内容。"



# ---------------- 新功能底层（批量试通/导入导出/单价/托盘） ----------------

def trial_one(address, api_key, model, proxy=None, timeout=20):
    """单个模型试通，返回 dict。"""
    base = address.strip().rstrip("/")
    ep = (base + "/responses") if base.lower().endswith("/v1") else (base + "/v1/responses")
    proxy = proxy or None
    seq = [(proxy, None), (proxy, SPOOF_HEADERS)] if proxy else [(None, None), (None, SPOOF_HEADERS)]
    last = ""
    for p, extra in seq:
        how = "经代理" if p else "直连"
        if extra:
            how += "+伪装"
        t0 = time.perf_counter()
        ok, code, text = _post_json(ep, api_key, {"model": model, "input": "ping", "stream": False},
                                    proxy=p, extra=extra, timeout=timeout)
        sec = round(time.perf_counter() - t0, 2)
        if ok:
            return {"ok": True, "how": how, "sec": sec, "err": "", "text": text[:80]}
        last = text if isinstance(text, str) else str(text)
    return {"ok": False, "how": "", "sec": 0, "err": last, "text": ""}


def trial_many(address, api_key, models, proxy=None, workers=3, timeout=20):
    """并发对多个模型试通。返回 [{model, ok, how, sec, err}]。"""
    from concurrent.futures import ThreadPoolExecutor
    ms = []
    for m in models:
        m = str(m or "").strip()
        if m and m not in ms:
            ms.append(m)
    if not ms:
        return []
    def run(m):
        r = trial_one(address, api_key, m, proxy=proxy, timeout=timeout)
        r["model"] = m
        return r
    out = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for r in ex.map(run, ms):
            out.append(r)
    return out


PRICE_FILE = os.path.join(DATA_DIR, "usage_cfg.json")


def load_price():
    try:
        if os.path.exists(PRICE_FILE):
            with open(PRICE_FILE, "r", encoding="utf-8") as f:
                return float(json.load(f).get("per_million", 0.0) or 0.0)
    except Exception:
        pass
    return 0.0


def save_price(per_million):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(PRICE_FILE, "w", encoding="utf-8") as f:
            json.dump({"per_million": float(per_million)}, f, ensure_ascii=False, indent=2)
        return True, ""
    except Exception as e:
        return False, str(e)


def export_profiles_to(path, include_keys=True):
    d = load_profiles()
    if not include_keys:
        d = {k: {kk: ("" if kk == "api_key" else vv) for kk, vv in v.items()}
             for k, v in d.items()}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    return len(d), path


def import_profiles_from(path, overwrite=True):
    with open(path, "r", encoding="utf-8") as f:
        d = json.load(f)
    if not isinstance(d, dict):
        raise ValueError("文件格式不对：应为方案字典")
    cur = load_profiles()
    added = 0
    for k, v in d.items():
        if not isinstance(v, dict):
            continue
        if k in cur and not overwrite:
            continue
        cur[k] = v
        added += 1
    save_profiles(cur)
    return added, len(d)


HAS_TRAY = False
try:
    import pystray
    from PIL import Image, ImageDraw
    HAS_TRAY = True
except Exception:
    HAS_TRAY = False


def make_tray_image():
    img = Image.new("RGB", (64, 64), (26, 115, 232))
    d = ImageDraw.Draw(img)
    d.text((14, 16), "RS", fill="white")
    return img



# ---------------- GUI ----------------
# 界面结构：两个页签
#   🏠 首页 · 新建连接 —— 一进来就能填新站点(地址/Key/网络/模型)，直接连接或存为新方案
#   🗂 方案区 · 管理切换 —— 列出已存方案，可调整、切换、复制、改名、删除、恢复

ACTIVE_FILE = os.path.join(DATA_DIR, "active.txt")


def read_active():
    try:
        if os.path.exists(ACTIVE_FILE):
            n = read_text(ACTIVE_FILE).strip()
            if n:
                return n
    except Exception:
        pass
    d = load_profiles()
    return sorted(d.keys())[0] if d else ""


def write_active(name):
    try:
        ensure_data_dir()
        write_text(ACTIVE_FILE, name)
    except Exception:
        pass


class ScrollQ(ttk.Frame):
    """可滚动容器：inner 里放问卷，滚轮上下翻。"""
    def __init__(self, master):
        super().__init__(master)
        self.canvas = tk.Canvas(self, highlightthickness=0)
        sb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = ttk.Frame(self.canvas)
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)


class ModelPicker(tk.Toplevel):
    """弹出模型多选窗口：分「该站真实模型 / 内置近一年主流」，可多选、可关键字过滤。"""
    def __init__(self, master, groups, current=()):
        super().__init__(master)
        self.title("选择模型（可多选）")
        self.geometry("560x600")
        self.minsize(480, 420)
        self.chosen = []
        self.groups = [[g, list(v)] for (g, v) in groups if v]
        current = set(current or [])
        ttk.Label(self, text="可多选：点一下就勾上/取消；确定后第一个=Codex 默认用，其余在 Codex 下拉里可切换。",
                  wraplength=520, justify="left").pack(anchor="w", padx=12, pady=(10, 2))
        ttk.Label(self, text="输入关键字过滤（如 gpt / claude / deepseek）：", wraplength=520,
                  justify="left").pack(anchor="w", padx=12)
        self.filter_var = tk.StringVar()
        e = ttk.Entry(self, textvariable=self.filter_var)
        e.pack(fill="x", padx=12, pady=(2, 4))
        self.filter_var.trace_add("write", lambda *a: self._refresh())
        wrap = ttk.Frame(self)
        wrap.pack(fill="both", expand=True, padx=12)
        sb = ttk.Scrollbar(wrap, orient="vertical")
        self.lb = tk.Listbox(wrap, selectmode="multiple", yscrollcommand=sb.set,
                             font=("Microsoft YaHei UI", 10), activestyle="dotbox")
        sb.config(command=self.lb.yview)
        sb.pack(side="right", fill="y")
        self.lb.pack(side="left", fill="both", expand=True)
        self.lb.bind("<Double-Button-1>", lambda ev: self._ok())
        self.lb.bind("<<ListboxSelect>>", self._on_select)
        self._cache = []
        btns = ttk.Frame(self)
        btns.pack(fill="x", padx=12, pady=8)
        ttk.Button(btns, text="确定", command=self._ok).pack(side="right", padx=6)
        ttk.Button(btns, text="取消", command=self.destroy).pack(side="right")
        self._refresh()
        # 预勾选当前已选的模型
        for i, (kind, txt) in enumerate(self._cache):
            if kind == "m" and txt in current:
                self.lb.selection_set(i)
        try:
            self.transient(master.winfo_toplevel())
            self.grab_set()
            self.lift()
            self.attributes("-topmost", True)
        except Exception:
            pass
        e.focus_set()

    def _items(self):
        f = self.filter_var.get().strip().lower()
        items = []
        for gname, mods in self.groups:
            shown = [m for m in mods if not f or f in m.lower()]
            if not shown:
                continue
            items.append(("h", "—— " + gname + " ——"))
            for m in shown:
                items.append(("m", m))
        return items

    def _refresh(self):
        self.lb.delete(0, "end")
        self._cache = []
        for kind, txt in self._items():
            self._cache.append((kind, txt))
            self.lb.insert("end", txt)

    def _on_select(self, ev):
        # 不允许选中分组标题行
        for i in list(self.lb.curselection()):
            if self._cache[i][0] == "h":
                self.lb.selection_clear(i)

    def _ok(self):
        chosen = []
        for i in self.lb.curselection():
            kind, txt = self._cache[i]
            if kind == "m" and txt not in chosen:
                chosen.append(txt)
        self.chosen = chosen
        self.destroy()

class Questionnaire(ttk.Frame):
    """一份问卷：标题在上、输入在下（上下排列），整行输入不截字。"""
    def __init__(self, master):
        super().__init__(master)
        self.v_mode = tk.StringVar(value="relay")
        self.v_address = tk.StringVar()
        self.v_key = tk.StringVar()
        self.v_network = tk.StringVar(value="proxy")
        self.v_model = tk.StringVar()
        self.v_proxy = tk.StringVar(value=P.get("proxy_url") or "")
        self.addr_hint = tk.StringVar()
        self.key_shown = False
        self.model_groups = [("内置 · 近一年主流模型", list(BUILTIN_MODELS))]
        self._model_list = []
        self.batch_trial_cb = None
        self._build()

    # ---- 基础组件 ----
    def _section(self, title, sub=None):
        ttk.Label(self, text=title, font=("Microsoft YaHei UI", 12, "bold")).pack(anchor="w", pady=(12, 2))
        if sub:
            ttk.Label(self, text=sub, foreground="#777", wraplength=700, justify="left").pack(anchor="w", pady=(0, 2))

    def _opt(self, parent, text, value, var):
        return ttk.Radiobutton(parent, text=text, value=value, variable=var).pack(anchor="w", pady=2)

    def _entry(self, show=None, font=("Microsoft YaHei UI", 11)):
        w = ttk.Entry(self, font=font, show=show)
        w.pack(fill="x", ipady=2)
        return w

    def _build(self):
        # 1 连接模式
        self._section("1. 连接模式", "中继模式：通过本机中继转发（适合需要伪装/代理的站）；直连模式：标准 OpenAI 兼容站，Codex 直接连")
        self._opt(self, "本机中继模式", "relay", self.v_mode)
        self._opt(self, "直连模式", "direct", self.v_mode)

        # 2 地址
        self._section("2. 中转站地址（必填）")
        self.e_addr = self._entry()
        self.l_hint = ttk.Label(self, textvariable=self.addr_hint, foreground="#888", wraplength=700, justify="left")
        self.l_hint.pack(anchor="w", pady=(2, 0))

        # 3 key
        self._section("3. API Key（必填，新站发给你的钥匙）")
        kf = ttk.Frame(self)
        kf.pack(fill="x")
        self.e_key = ttk.Entry(kf, font=("Microsoft YaHei UI", 11), show="●")
        self.e_key.pack(side="left", fill="x", expand=True, ipady=2)
        ttk.Button(kf, text="显示/隐藏", command=self._toggle_key).pack(side="left", padx=(6, 0))

        # 4 网络
        self._section("4. 网络方式", "只有中继模式需要选；可先点底部「测连通」看这个站怎么连才通。")
        self._opt(self, "直连（不用代理）", "direct", self.v_network)
        self._opt(self, "走本地代理", "proxy", self.v_network)
        pf = ttk.Frame(self)
        pf.pack(fill="x")
        self.e_proxy = ttk.Entry(pf, font=("Microsoft YaHei UI", 11), textvariable=self.v_proxy)
        self.e_proxy.pack(side="left", fill="x", expand=True, ipady=2)
        ttk.Label(self, text="代理地址（选「走本地代理」时用；如 http://127.0.0.1:端口，不知道可留空）",
                  foreground="#888", wraplength=700, justify="left").pack(anchor="w", pady=(2, 0))

        # 5 模型
        self._section("5. 模型（点按钮挑，不用手输）")
        row = ttk.Frame(self)
        row.pack(fill="x", pady=(2, 0))
        self.l_model = ttk.Label(row, text="已选模型：未选择", font=("Microsoft YaHei UI", 11, "bold"),
                                 foreground="#0a6", wraplength=520, justify="left")
        self.l_model.pack(side="left", anchor="w")
        ttk.Button(row, text="选择模型 …", command=self.open_picker).pack(side="left", padx=(10, 0))
        ttk.Label(self, text="可多选几个；点「立即连接/存为新方案」后会自动写进 Codex 的模型下拉，在 Codex 顶部就能显示和切换。",
                  foreground="#888", wraplength=700, justify="left").pack(anchor="w", pady=(3, 0))
        brow = ttk.Frame(self)
        brow.pack(fill="x", pady=(4, 2))
        self.btn_batch_trial = ttk.Button(brow, text="批量试通这些模型（只留能用的）", command=self._batch_click)
        self.btn_batch_trial.pack(side="left")
        self._sync()
        self._refresh_model_label()

    def _sync(self):
        if self.v_mode.get() == "relay":
            self.addr_hint.set("例：https://上游站域名（中继模式填上游根域名）")
        else:
            self.addr_hint.set("例：https://你的站.com/v1（直连模式填 Codex 要连的完整地址）")

    def _toggle_key(self):
        self.key_shown = not self.key_shown
        self.e_key.configure(show="" if self.key_shown else "●")

    def _refresh_model_label(self):
        chosen = self._model_list
        if chosen:
            self.l_model.configure(text="已选 %d 个模型 · 默认用：%s" % (len(chosen), chosen[0]))
        else:
            self.l_model.configure(text="已选模型：未选择")

    def open_picker(self):
        mp = ModelPicker(self, self.model_groups, current=self._model_list)
        self.wait_window(mp)
        if mp.chosen:
            self._model_list = list(mp.chosen)
            self.v_model.set(self._model_list[0])
            self._refresh_model_label()

    def reset_builtin(self):
        self.model_groups = [("内置 · 近一年主流模型", list(BUILTIN_MODELS))]

    def set_batch_cb(self, cb):
        self.batch_trial_cb = cb

    def _batch_click(self):
        if self.batch_trial_cb:
            self.batch_trial_cb(self)

    def set_station_models(self, models):
        """把该站真实模型放最上面，内置近一年模型放下面，两边都在。"""
        ms = []
        for m in models:
            if m and m not in ms:
                ms.append(m)
        if ms:
            self.model_groups = [("该站真实模型", ms)]
            rest = [m for m in BUILTIN_MODELS if m not in ms]
            if rest:
                self.model_groups.append(("内置 · 近一年主流模型", rest))
        else:
            self.reset_builtin()

    def auto_fill_if_empty(self):
        if not self.v_model.get().strip():
            for gname, mods in self.model_groups:
                if mods:
                    self._model_list = [mods[0]]
                    self.v_model.set(mods[0])
                    break
        self._refresh_model_label()

    def values(self):
        model = self.v_model.get().strip()
        models = [m for m in self._model_list if m]
        if model and model not in models:
            models = [model] + models
        return {"mode": self.v_mode.get(), "address": self.v_address.get().strip(),
                "api_key": self.v_key.get().strip(), "network": self.v_network.get(),
                "proxy": self.v_proxy.get().strip(),
                "model": model,
                "models": models}

    def load(self, d):
        self.v_mode.set(d.get("mode", "relay"))
        self.v_address.set(d.get("address", ""))
        self.v_key.set(d.get("api_key", ""))
        self.v_network.set(d.get("network", "proxy"))
        self.v_proxy.set(d.get("proxy", P.get("proxy_url") or ""))
        model = d.get("model", "")
        models = d.get("models")
        if isinstance(models, list):
            models = [str(x) for x in models if x]
        else:
            models = [model] if model else []
        self._model_list = models
        self.v_model.set(model or (models[0] if models else ""))
        self._sync()
        self._refresh_model_label()

class App:
    def __init__(self, root):
        self.root = root
        root.title("Codex 中转站切换助手")
        root.geometry("1000x800")
        try:
            root.tk.call("tk", "scaling", 1.15)
        except Exception:
            pass
        self.profiles = ensure_default_profile()
        self.active_name = read_active() or sorted(self.profiles.keys())[0] if self.profiles else ""
        self.canvas_now = None
        self._build()
        self._refresh_scheme_list()
        self._bind_wheel()
        self._on_tab()
        if HAS_TRAY:
            self.root.protocol("WM_DELETE_WINDOW", self._on_close_win)
        self.log("欢迎使用 Codex 中转站切换助手\n「首页」直接填新站信息；「方案区」管理/切换已有方案。")

    # ---------- 界面骨架 ----------
    def _build(self):
        outer = ttk.Frame(self.root, padding=8)
        outer.pack(fill="both", expand=True)
        self.nb = ttk.Notebook(outer)
        self.nb.pack(fill="both", expand=True)
        self.nb.bind("<<NotebookTabChanged>>", lambda e: self._on_tab())

        self._build_home(self.nb)
        self._build_manage(self.nb)
        self._build_usage(self.nb)
        self._build_logtab(self.nb)

        # 底部运行记录
        bar = ttk.Frame(outer)
        bar.pack(fill="x", pady=(6, 1))
        ttk.Label(bar, text="运行记录：", foreground="#555").pack(side="left")
        ttk.Button(bar, text="隐藏到托盘", command=self.mini_to_tray).pack(side="right", padx=4)
        ttk.Button(bar, text="一键重启 Codex", command=self.do_restart_codex).pack(side="right")
        self.report = scrolledtext.ScrolledText(outer, height=8, state="disabled", font=("Microsoft YaHei UI", 9))
        self.report.pack(fill="x")

    def _build_home(self, nb):
        f = ttk.Frame(nb, padding=6)
        nb.add(f, text="🏠 首页 · 新建连接")
        self.l_status = ttk.Label(f, foreground="#0a7a2f")
        self.l_status.pack(anchor="w", pady=(0, 4))
        ttk.Label(f, text="直接往下填新站点，填完点「立即连接」就切过去；想存起来以后用，点「存为新方案」。",
                  foreground="#666").pack(anchor="w", pady=(0, 4))
        sq = ScrollQ(f)
        sq.pack(fill="both", expand=True)
        self.q1 = Questionnaire(sq.inner)
        self.q1.pack(fill="x")
        self.q1.set_batch_cb(self.batch_trial)
        btns = ttk.Frame(f)
        btns.pack(fill="x", pady=(4, 0))
        ttk.Button(btns, text="立即连接（切换生效）", command=self.home_connect).pack(side="left", padx=4)
        ttk.Button(btns, text="试通（真实发一条）", command=self.home_trial).pack(side="left", padx=4)
        ttk.Button(btns, text="存为新方案", command=self.home_save).pack(side="left", padx=4)
        ttk.Button(btns, text="测连通", command=lambda: self.do_test(self.q1)).pack(side="left", padx=4)
        ttk.Button(btns, text="获取该站真实模型", command=lambda: self.do_fetch(self.q1)).pack(side="left", padx=4)
        self.home_sq = sq

    def _build_manage(self, nb):
        f = ttk.Frame(nb, padding=6)
        nb.add(f, text="🗂 方案区 · 管理切换")
        left = ttk.LabelFrame(f, text="已有方案（点选载入）", padding=6)
        left.pack(side="left", fill="y", padx=(0, 8))
        lbwrap = ttk.Frame(left)
        lbwrap.pack(fill="both", expand=True)
        sb = ttk.Scrollbar(lbwrap, orient="vertical")
        self.lb = tk.Listbox(lbwrap, height=14, width=30, yscrollcommand=sb.set)
        sb.config(command=self.lb.yview)
        sb.pack(side="right", fill="y")
        self.lb.pack(side="left", fill="both", expand=True)
        self.lb.bind("<<ListboxSelect>>", self.on_scheme_select)
        ttk.Label(left, text="当前使用中：", foreground="#0a7a2f").pack(anchor="w", pady=(6, 0))
        self.l_active = ttk.Label(left, text="", foreground="#0a7a2f")
        self.l_active.pack(anchor="w")

        right = ttk.Frame(f)
        right.pack(side="left", fill="both", expand=True)
        sq = ScrollQ(right)
        sq.pack(fill="both", expand=True)
        self.q2 = Questionnaire(sq.inner)
        self.q2.pack(fill="x")
        self.q2.set_batch_cb(self.batch_trial)
        self.l_sel = ttk.Label(right, text="（先在左边点选一个方案）", foreground="#888")
        self.l_sel.pack(anchor="w", pady=(2, 0))
        btns = ttk.Frame(right)
        btns.pack(fill="x", pady=(4, 0))
        ttk.Button(btns, text="保存改动", command=self.save_changes).pack(side="left", padx=3)
        ttk.Button(btns, text="切换到该方案", command=self.switch_selected).pack(side="left", padx=3)
        ttk.Button(btns, text="体检所有方案（推荐最优）", command=self.health_all).pack(side="left", padx=3)
        ttk.Button(btns, text="试通此方案", command=self.scheme_trial).pack(side="left", padx=3)
        ttk.Button(btns, text="复制成新方案", command=self.duplicate_selected).pack(side="left", padx=3)
        ttk.Button(btns, text="改名", command=self.rename_selected).pack(side="left", padx=3)
        ttk.Button(btns, text="删除", command=self.delete_selected).pack(side="left", padx=3)
        ttk.Button(btns, text="恢复上次配置", command=self.do_restore).pack(side="left", padx=3)
        btns2 = ttk.Frame(right)
        btns2.pack(fill="x", pady=(3, 0))
        ttk.Button(btns2, text="切到最优方案（先体检）", command=self.health_and_switch_best).pack(side="left", padx=3)
        ttk.Button(btns2, text="导出方案", command=self.export_profiles_ui).pack(side="left", padx=3)
        ttk.Button(btns2, text="导入方案", command=self.import_profiles_ui).pack(side="left", padx=3)
        ttk.Label(right, text="改完记得点「保存改动」；「切换」会写 C 盘并重启中继(有自动备份)。",
                  foreground="#888").pack(anchor="w", pady=(4, 0))
        self.manage_sq = sq

    def _build_usage(self, nb):
        f = ttk.Frame(nb, padding=6)
        nb.add(f, text="用量统计")
        ttk.Label(f, text="统计本机 Codex 的 token 用量（只读本机会话日志，不联网）。", foreground="#666").pack(anchor="w")
        self.txt_usage = scrolledtext.ScrolledText(f, height=16, state="disabled", font=("Consolas", 10))
        self.txt_usage.pack(fill="both", expand=True, pady=(6, 4))
        btns = ttk.Frame(f)
        btns.pack(fill="x")
        ttk.Button(btns, text="刷新统计（近 7 天）", command=lambda: self.refresh_usage(7)).pack(side="left", padx=4)
        ttk.Button(btns, text="近 30 天", command=lambda: self.refresh_usage(30)).pack(side="left", padx=4)
        pricef = ttk.Frame(f)
        pricef.pack(fill="x", pady=(2, 0))
        ttk.Label(pricef, text="参考单价（元/百万tokens，用于费用估算）：").pack(side="left")
        self.v_price = tk.StringVar(value=str(load_price()))
        ttk.Entry(pricef, textvariable=self.v_price, width=10).pack(side="left", padx=4)
        ttk.Button(pricef, text="保存单价", command=self.save_price_ui).pack(side="left", padx=4)
        ttk.Label(pricef, text="0 = 不算费用", foreground="#888").pack(side="left", padx=4)


    def _usage_show(self, txt):
        self.txt_usage.configure(state="normal")
        self.txt_usage.delete("1.0", "end")
        self.txt_usage.insert("1.0", txt)
        self.txt_usage.configure(state="disabled")

    def refresh_usage(self, days=7):
        self.log("正在扫描本地 Codex 用量（最近 %d 天）..." % days)
        def work():
            return scan_token_usage(days)
        def done(res):
            txt, nums = res
            price = load_price()
            if price and price > 0:
                c24 = round(nums["24h"] / 1000000.0 * price, 2)
                c7 = round(nums["7d"] / 1000000.0 * price, 2)
                txt += "\n\n费用估算（按 %s 元/百万tokens）：\n近24h ≈ %s 元\n近7天 ≈ %s 元" % (price, c24, c7)
            self._usage_show(txt)
            self.log("用量统计刷新完成：近24h %s / 近%d天 %s tokens" % (format(nums["24h"], ","), days, format(nums["7d"], ",")))
        self._run_async(work, done)


    def _build_logtab(self, nb):
        f = ttk.Frame(nb, padding=6)
        nb.add(f, text="日志")
        ttk.Label(f, text="操作日志（存在本机 _data\\操作日志.txt，带时间戳；不联网、不进仓库）。",
                  foreground="#666").pack(anchor="w")
        self.txt_log = scrolledtext.ScrolledText(f, height=18, state="disabled", font=("Consolas", 10))
        self.txt_log.pack(fill="both", expand=True, pady=(6, 4))
        btns = ttk.Frame(f)
        btns.pack(fill="x")
        ttk.Button(btns, text="刷新日志", command=self.refresh_log_view).pack(side="left", padx=4)
        ttk.Button(btns, text="打开日志文件夹", command=self.open_log_dir).pack(side="left", padx=4)
        ttk.Button(btns, text="清空日志", command=self.clear_log).pack(side="left", padx=4)

    def _read_log_tail(self, n=2000):
        if not os.path.exists(LOG_FILE_PATH):
            return []
        try:
            with open(LOG_FILE_PATH, "r", encoding="utf-8", errors="ignore") as f:
                all_lines = f.readlines()
            return all_lines[-n:]
        except Exception:
            return []

    def refresh_log_view(self):
        lines = self._read_log_tail()
        self.txt_log.configure(state="normal")
        self.txt_log.delete("1.0", "end")
        if lines:
            self.txt_log.insert("1.0", "".join(lines))
            self.txt_log.see("end")
        else:
            self.txt_log.insert("1.0", "（还没有日志。执行任意操作后会自动记录。）")
        self.txt_log.configure(state="disabled")

    def open_log_dir(self):
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            os.startfile(DATA_DIR)
        except Exception as e:
            messagebox.showerror("提示", "打开日志文件夹失败：%s" % e)

    def clear_log(self):
        if not messagebox.askyesno("清空日志", "确定清空全部操作日志？"):
            return
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            with open(LOG_FILE_PATH, "w", encoding="utf-8") as f:
                f.write("")
            self.refresh_log_view()
            self.log("操作日志已清空。")
        except Exception as e:
            messagebox.showerror("提示", "清空失败：%s" % e)


    # ---------- 状态 ----------
    def _bind_wheel(self):
        self.root.bind_all("<MouseWheel>", self._wheel)

    def _wheel(self, e):
        c = self.canvas_now
        if c is not None:
            c.yview_scroll(int(-e.delta / 120), "units")

    def _on_tab(self, *a):
        try:
            cur = self.nb.index(self.nb.select())
        except Exception:
            cur = 0
        if cur >= 2:
            self.canvas_now = None
        else:
            self.canvas_now = self.home_sq.canvas if cur == 0 else self.manage_sq.canvas
        self._refresh_status()

    def _refresh_status(self):
        n = self.active_name or "（暂无）"
        self.l_status.configure(text="● 当前正在用：" + n)
        self.l_active.configure(text=n)

    def _refresh_scheme_list(self):
        self.profiles = load_profiles()
        self.lb.delete(0, "end")
        for name in sorted(self.profiles.keys()):
            mark = "★ " if name == self.active_name else "   "
            self.lb.insert("end", mark + name)
        self._refresh_status()

    # ---------- 工具 ----------
    def log(self, txt):
        self.report.configure(state="normal")
        self.report.insert("end", txt + "\n")
        self.report.see("end")
        self.report.configure(state="disabled")
        write_log(txt)

    def busy(self, on):
        self.root.configure(cursor="watch" if on else "")

    def _run_async(self, fn, done_cb=None):
        self.busy(True)
        def worker():
            try:
                res = fn()
                if done_cb:
                    self.root.after(0, lambda: (done_cb(res), self.busy(False)))
                else:
                    self.root.after(0, self.busy_off)
            except Exception as e:
                self.root.after(0, lambda: (self.log("❌ 异常：" + str(e)), self.busy(False)))
        threading.Thread(target=worker, daemon=True).start()

    def busy_off(self):
        self.busy(False)

    def _validate(self, vals, allow_empty_model=False):
        miss = []
        if not vals["address"]:
            miss.append("中转站地址")
        if not vals["api_key"]:
            miss.append("API Key")
        if not vals["model"] and not allow_empty_model:
            miss.append("模型(请在⑤里选一个)")
        return miss

    def _apply_vals(self, vals, log_name):
        miss = self._validate(vals)
        if miss:
            messagebox.showwarning("还差几项", "请先填：\n" + "\n".join(miss))
            return
        mode_txt = "本机中继模式" if vals["mode"] == "relay" else "直连模式"
        tip = ("即将对「%s」执行一键切换（会自动备份，可随时点「恢复上次配置」）：\n\n"
               "· 模式：%s\n· 地址：%s\n· 模型：%s\n· 网络：%s\n\n"
               "执行会写 C 盘配置并重启本地中继。\n改完请重启 Codex 或新开对话后生效。\n\n确认切换？"
               % (log_name, mode_txt, vals["address"], vals["model"],
                  "走本地代理" if vals["network"] == "proxy" else "直连"))
        if not messagebox.askyesno("一键切换", tip):
            return
        self.log("==== 开始切换：%s ====" % log_name)
        def work():
            ok, rep = apply_profile(vals, None)
            return ok, rep
        def done(res):
            ok, rep = res
            self.log(rep)
            self.log("==== 切换" + ("成功 ✅" if ok else "有失败，请看上面 ❌") + " ====")
            if ok:
                self.active_name = log_name
                write_active(log_name)
                self._refresh_scheme_list()
        self._run_async(work, done)

    def _current_sel_name(self):
        sel = self.lb.curselection()
        if not sel:
            return None
        raw = self.lb.get(sel[0])
        return raw[2:] if raw.startswith(("★ ", "   ")) else raw

    # ---------- 首页动作 ----------
    def home_connect(self):
        vals = self.q1.values()
        self._apply_vals(vals, vals.get("name") or "临时连接(未存方案)")

    def home_save(self):
        vals = self.q1.values()
        miss = self._validate(vals)
        if miss:
            messagebox.showwarning("还差几项", "请先填：\n" + "\n".join(miss))
            return
        import tkinter.simpledialog as sd
        name = sd.askstring("存为新方案", "给这个方案起个名字：", parent=self.root)
        if not name or not name.strip():
            return
        name = name.strip()
        if name in self.profiles:
            if not messagebox.askyesno("同名", "已存在「%s」，覆盖它？" % name):
                return
        p = dict(vals)
        p.pop("name", None)
        self.profiles[name] = p
        save_profiles(self.profiles)
        self._refresh_scheme_list()
        self.active_name = self.active_name or name
        self.nb.select(1)
        self._select_in_list(name)
        self.log("已保存新方案：%s（可在「方案区」看到）" % name)

    def do_test(self, q):
        vals = q.values()
        if not vals["address"]:
            messagebox.showwarning("提示", "先填中转站地址。")
            return
        self.log("正在测 " + vals["address"] + " ...")
        def work():
            return test_connectivity(vals["address"], vals["api_key"], vals.get("proxy") or P.get("proxy_url"))
        def done(res):
            self.log(res)
            messagebox.showinfo("测连通结果", res)
        self._run_async(work, done)

    def do_fetch(self, q):
        vals = q.values()
        if not vals["address"]:
            messagebox.showwarning("提示", "先填中转站地址。")
            return
        self.log("正在获取该站真实模型（自动尝试 直连/代理/伪装 直到成功）...")
        def work():
            return probe(vals["address"], vals["api_key"], proxy=vals.get("proxy") or P.get("proxy_url"))
        def done(res):
            ok, how, models = res
            if ok and models:
                q.set_station_models(models)
                q.auto_fill_if_empty()
                self.log("获取成功（%s）：%d 个模型，已在「选择模型」里排到「该站真实模型」组。" % (how, len(models)))
                messagebox.showinfo("获取成功", "已获取 %d 个模型（%s）。\n\n点「选择模型」可见两组：\n· 该站真实模型（最上面）\n· 内置 · 近一年主流模型（下面）\n\n前几个：%s"
                                    % (len(models), how, ", ".join(models[:8])))
            else:
                q.reset_builtin()
                self.log("该站没返回模型列表：%s（内置近一年主流模型仍可在「选择模型」里挑）" % how)
                messagebox.showwarning("获取失败", "该站没返回模型列表：%s\n\n内置近一年主流模型仍可在「选择模型」里挑。" % how)
        self._run_async(work, done)
    # ---------- 新功能动作（试通/体检/重启） ----------
    def do_trial_q(self, q):
        vals = q.values()
        miss = []
        if not vals["address"]:
            miss.append("中转站地址")
        if not vals["api_key"]:
            miss.append("API Key")
        if not vals["model"]:
            miss.append("模型")
        if miss:
            messagebox.showwarning("还差几项", "试通前请先填：\n" + "\n".join(miss))
            return
        self.log("正在试通（真实发一条最小请求，模型 %s）..." % vals["model"])
        proxy = vals.get("proxy") if vals.get("network") == "proxy" else None
        def work():
            return trial(vals["address"], vals["api_key"], vals["model"], proxy=proxy)
        def done(res):
            self.log(res)
            messagebox.showinfo("试通结果", res)
        self._run_async(work, done)

    def home_trial(self):
        self.do_trial_q(self.q1)

    def scheme_trial(self):
        name = self._current_sel_name()
        if not name:
            messagebox.showwarning("提示", "先在左边选中要试通的方案。")
            return
        self.do_trial_q(self.q2)

    def health_all(self):
        profs = load_profiles()
        items = [(nm, p) for nm, p in profs.items()
                 if (p.get("address") or "").strip() and (p.get("api_key") or "").strip()]
        if not items:
            messagebox.showwarning("提示", "没有可体检的方案（需要方案填了地址和 Key）。")
            return
        self.log("开始体检 %d 个方案（并发测连通/延迟）..." % len(items))
        def work():
            from concurrent.futures import ThreadPoolExecutor
            def testone(item):
                nm, p = item
                proxy = (p.get("proxy") or P.get("proxy_url")) if p.get("network") == "proxy" else None
                return nm, health_one(p["address"], p["api_key"], proxy=proxy)
            results = {}
            with ThreadPoolExecutor(max_workers=4) as ex:
                for nm, res in ex.map(testone, items):
                    results[nm] = res
            return results
        def done(results):
            lines = []
            oklist = []
            for nm, p in items:
                r = results.get(nm)
                if r and r.get("ok"):
                    oklist.append((nm, r))
                    lines.append("✅ %s → %s，%s 秒，模型 %d 个" % (nm, r["how"], r["sec"], r["models"]))
                else:
                    err = (r or {}).get("err", "未知")
                    lines.append("❌ %s → 不通（%s）" % (nm, str(err)[:100]))
            rec = "没有可用方案"
            if oklist:
                oklist.sort(key=lambda x: x[1]["sec"])
                best = oklist[0]
                rec = "建议先用「%s」（%s，%.1f 秒）" % (best[0], best[1]["how"], best[1]["sec"])
            msg = "\n".join(lines) + "\n\n★ " + rec
            for ln in lines:
                self.log(ln)
            self.log("★ " + rec)
            messagebox.showinfo("体检结果", msg)
        self._run_async(work, done)

    def do_restart_codex(self):
        if not messagebox.askyesno("重启 Codex", "会先关闭 Codex 应用再重新打开。\n若此对话正开在 Codex 里，会被关掉（重开后可在会话列表找回）。\n确认重启？"):
            return
        self.log("正在重启 Codex...")
        def work():
            return restart_codex()
        def done(res):
            ok, msg = res
            self.log("重启 Codex → " + msg)
            messagebox.showinfo("重启结果", msg)
        self._run_async(work, done)


    # ---------- 新功能动作（批量试通/切最优/导入导出/托盘/单价） ----------
    def batch_trial(self, q):
        vals = q.values()
        models = [m for m in vals.get("models") or [] if m] or ([vals.get("model")] if vals.get("model") else [])
        if not vals["address"] or not vals["api_key"]:
            messagebox.showwarning("提示", "请先填 中转站地址 和 API Key 再批量试通。")
            return
        if not models:
            messagebox.showwarning("提示", "请先选至少一个模型。")
            return
        if not messagebox.askyesno("批量试通", "会向 %d 个模型各真发一条最小请求，稍等片刻。\n最后只保留能用的模型。\n确认？" % len(models)):
            return
        self.log("开始批量试通 %d 个模型..." % len(models))
        proxy = vals.get("proxy") if vals.get("network") == "proxy" else None
        def work():
            return trial_many(vals["address"], vals["api_key"], models, proxy=proxy)
        def done(res):
            oklist, badlist = [], []
            for r in res:
                if r["ok"]:
                    oklist.append(r["model"])
                    self.log("✅ %s 能用（%s %.1fs）" % (r["model"], r["how"], r["sec"]))
                else:
                    badlist.append(r["model"])
                    self.log("❌ %s 不通（%s）" % (r["model"], str(r.get("err"))[:80]))
            do_remove = False
            if oklist and badlist:
                do_remove = messagebox.askyesno(
                    "检测完成",
                    "能用 %d 个，不通 %d 个。\n不通可能是：余额/配额用尽、该模型暂不可用或 Key 无权用。\n\n要把不通的移出当前选择吗？" % (len(oklist), len(badlist)))
            elif not oklist:
                messagebox.showwarning("检测完成", "%d 个全都不通。\n请先确认余额/Key/模型名，别急着删。原选择已保留。" % len(badlist))
            if oklist and (do_remove or not badlist):
                q._model_list = oklist
                q.v_model.set(oklist[0])
                q._refresh_model_label()
            msg = "能用 %d 个：%s\n不通 %d 个：%s\n\n%s" % (
                len(oklist), ", ".join(oklist[:8]) + ("…" if len(oklist) > 8 else ""),
                len(badlist), ", ".join(badlist[:8]) + ("…" if len(badlist) > 8 else ""),
                "已把能用的设为模型列表（第一个=默认）" if (oklist and (do_remove or not badlist)) else "当前选择保持原样，你自己决定留哪些。")
            self.log("批量试通完成：能用 %d / 共 %d" % (len(oklist), len(res)))
            messagebox.showinfo("批量试通结果", msg)
        self._run_async(work, done)

    def health_and_switch_best(self):
        profs = load_profiles()
        items = [(nm, p) for nm, p in profs.items()
                 if (p.get("address") or "").strip() and (p.get("api_key") or "").strip()]
        if not items:
            messagebox.showwarning("提示", "没有可体检的方案。")
            return
        self.log("开始体检并挑选最优方案...")
        def work():
            from concurrent.futures import ThreadPoolExecutor
            def testone(item):
                nm, p = item
                proxy = (p.get("proxy") or P.get("proxy_url")) if p.get("network") == "proxy" else None
                return nm, health_one(p["address"], p["api_key"], proxy=proxy)
            results = {}
            with ThreadPoolExecutor(max_workers=4) as ex:
                for nm, res in ex.map(testone, items):
                    results[nm] = res
            ok = [(nm, results[nm]) for nm, _ in items if results.get(nm, {}).get("ok")]
            ok.sort(key=lambda x: x[1]["sec"])
            return ok
        def done(ok):
            if not ok:
                messagebox.showwarning("结果", "没有一个方案能通，无法自动切换。")
                return
            best_name, info = ok[0]
            lines = "体检结果（按快慢）：\n" + "\n".join("· %s → %s %.1fs" % (nm, inf["how"], inf["sec"]) for nm, inf in ok[:6])
            if not messagebox.askyesno("切到最优", "%s\n\n是否切换到最快方案「%s」？" % (lines, best_name)):
                return
            vals = dict(load_profiles()[best_name])
            self._apply_vals(vals, best_name)
        self._run_async(work, done)

    def export_profiles_ui(self):
        from tkinter import filedialog, messagebox as _mb
        if not load_profiles():
            _mb.showwarning("提示", "还没有任何方案可导出。")
            return
        inc = _mb.askyesno("导出", "导出文件里要包含 API Key 吗？\n选“否”会清空 Key（更安全，适合发给别人）")
        path = filedialog.asksaveasfilename(parent=self.root, title="导出方案",
                                            defaultextension=".json", initialfile="codex_switcher_profiles.json",
                                            filetypes=[("JSON", "*.json")])
        if not path:
            return
        try:
            n, p2 = export_profiles_to(path, include_keys=inc)
            self.log("已导出 %d 个方案 → %s（%s）" % (n, p2, "含Key" if inc else "不含Key"))
            _mb.showinfo("导出完成", "已导出 %d 个方案。\n%s" % (n, p2))
        except Exception as e:
            _mb.showerror("导出失败", str(e))

    def import_profiles_ui(self):
        from tkinter import filedialog
        path = filedialog.askopenfilename(parent=self.root, title="导入方案",
                                          filetypes=[("JSON", "*.json"), ("所有文件", "*.*")])
        if not path:
            return
        overwrite = messagebox.askyesno("导入", "同名方案是否覆盖？\n选“是”覆盖同名，选“否”跳过同名。")
        try:
            added, total = import_profiles_from(path, overwrite=overwrite)
            self.profiles = load_profiles()
            self._refresh_scheme_list()
            self.log("导入完成：文件共 %d 个方案，新增/更新 %d 个。" % (total, added))
            messagebox.showinfo("导入完成", "文件共 %d 个方案，新增/更新 %d 个。" % (total, added))
        except Exception as e:
            messagebox.showerror("导入失败", str(e))

    def save_price_ui(self):
        try:
            v = float(self.v_price.get().strip() or 0)
        except Exception:
            messagebox.showwarning("提示", "单价要填数字。")
            return
        ok, err = save_price(v)
        if ok:
            self.log("参考单价已保存：%s 元/百万tokens" % v)
            self.refresh_usage(7)
        else:
            messagebox.showerror("保存失败", err)

    # ---- 托盘（可选，装了 pystray 才有） ----
    def _on_close_win(self):
        if HAS_TRAY and getattr(self, "_tray", None) is not None:
            self.root.withdraw()
            return
        self.root.destroy()

    def mini_to_tray(self):
        if not HAS_TRAY:
            messagebox.showinfo("提示", "本机没装 pystray（pip install pystray），无法最小化到托盘。\n可先关窗口。")
            return
        self._ensure_tray()
        self.root.withdraw()

    def _ensure_tray(self):
        if getattr(self, "_tray", None) is not None:
            self._update_tray_menu()
            return
        from pystray import Icon, Menu, MenuItem
        self._tray_action = None
        def act(icon, item):
            name = item.text
            self.root.after(0, lambda: self._tray_click(name))
        def show(icon, item):
            self.root.after(0, self._tray_show)
        def quit(icon, item):
            self.root.after(0, self._tray_quit)
        menu = Menu(
            MenuItem("打开主窗口", show),
            Menu.SEPARATOR,
            MenuItem("隐藏到托盘提示：点右上 X 也会最小化到托盘", None, enabled=False),
            Menu.SEPARATOR,
            MenuItem("退出程序", quit),
        )
        icon = Icon("relay_switcher", make_tray_image(), "Codex 中转站切换助手", menu)
        self._tray = icon
        self._tray_static = (show, quit)
        threading.Thread(target=icon.run, daemon=True).start()

    def _tray_click(self, name):
        if name == "打开主窗口":
            self._tray_show()
            return
        if name == "退出程序":
            self._tray_quit()
            return
        if name.startswith("切换: "):
            nm = name[4:].strip()
            profs = load_profiles()
            if nm in profs:
                self._apply_vals(dict(profs[nm]), nm)
            return

    def _tray_show(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def _tray_quit(self):
        try:
            if getattr(self, "_tray", None) is not None:
                self._tray.stop()
        except Exception:
            pass
        self.root.after(300, self.root.destroy)

    def _update_tray_menu(self):
        pass  # 简化：托盘菜单在打开时不再动态重建；切换请用主窗口

    # ---------- 方案区动作 ----------
    def on_scheme_select(self, e):
        name = self._current_sel_name()
        if name and name in self.profiles:
            self.q2.load(self.profiles[name])
            self.l_sel.configure(text="正在调整：" + name)

    def _select_in_list(self, name):
        for i in range(self.lb.size()):
            raw = self.lb.get(i)
            nm = raw[2:] if raw.startswith(("★ ", "   ")) else raw
            if nm == name:
                self.lb.selection_clear(0, "end")
                self.lb.selection_set(i)
                self.lb.see(i)
                break

    def save_changes(self):
        name = self._current_sel_name()
        if not name:
            messagebox.showwarning("提示", "先在左边选中要保存的方案。")
            return
        vals = self.q2.values()
        miss = self._validate(vals)
        if miss:
            messagebox.showwarning("还差几项", "请先填：\n" + "\n".join(miss))
            return
        self.profiles[name] = dict(vals)
        save_profiles(self.profiles)
        self.log("已保存改动：%s" % name)

    def switch_selected(self):
        name = self._current_sel_name()
        if not name:
            messagebox.showwarning("提示", "先在左边选中要切换的方案。")
            return
        vals = dict(self.profiles[name])
        self._apply_vals(vals, name)

    def duplicate_selected(self):
        name = self._current_sel_name()
        if not name:
            messagebox.showwarning("提示", "先在左边选中要复制的方案。")
            return
        import tkinter.simpledialog as sd
        new = sd.askstring("复制成新方案", "新方案名字：", parent=self.root)
        if not new or not new.strip():
            return
        new = new.strip()
        if new in self.profiles:
            messagebox.showwarning("提示", "已存在同名方案。")
            return
        self.profiles[new] = dict(self.profiles[name])
        save_profiles(self.profiles)
        self._refresh_scheme_list()
        self._select_in_list(new)
        self.log("已复制方案：%s → %s" % (name, new))

    def rename_selected(self):
        name = self._current_sel_name()
        if not name:
            messagebox.showwarning("提示", "先在左边选中要改名的方案。")
            return
        import tkinter.simpledialog as sd
        new = sd.askstring("改名", "新名字：", initialvalue=name, parent=self.root)
        if not new or not new.strip() or new.strip() == name:
            return
        new = new.strip()
        if new in self.profiles:
            messagebox.showwarning("提示", "已存在同名方案。")
            return
        self.profiles[new] = self.profiles.pop(name)
        save_profiles(self.profiles)
        if self.active_name == name:
            self.active_name = new
            write_active(new)
        self._refresh_scheme_list()
        self._select_in_list(new)
        self.log("方案「%s」已改名为「%s」" % (name, new))

    def delete_selected(self):
        name = self._current_sel_name()
        if not name:
            messagebox.showwarning("提示", "先在左边选中要删除的方案。")
            return
        if not messagebox.askyesno("确认删除", "确定删除方案「%s」？" % name):
            return
        del self.profiles[name]
        save_profiles(self.profiles)
        if self.active_name == name:
            self.active_name = ""
            write_active("")
        self._refresh_scheme_list()
        self.log("已删除方案：%s" % name)

    def do_restore(self):
        if not messagebox.askyesno("恢复", "把上次切换前备份的配置恢复回去？"):
            return
        self.log("==== 开始恢复 ====")
        def work():
            return restore_last()
        def done(res):
            ok, rep = res
            self.log(rep)
            self.log("==== 恢复" + ("完成 ✅" if ok else "有失败 ❌") + " ====")
        self._run_async(work, done)


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        # 无界面自检：仅在临时目录里测关键读写函数
        import tempfile
        td = tempfile.mkdtemp(prefix="switcher_test_")
        # 模拟 config.toml
        cfg = os.path.join(td, "config.toml")
        write_text(cfg, 'model = "deepseek-v4-flash"\nmodel_provider = "openai-chat-completions"\nopenai_base_url = "http://127.0.0.1:8123/v1"\n\n[model_providers.openai-chat-completions]\nname = "OpenAI"\nbase_url = "http://127.0.0.1:8123/v1"\n')
        P["codex_config"] = cfg
        ok, b = set_codex_config("claude-opus-4-8", "http://127.0.0.1:8123/v1")
        t = read_text(cfg)
        assert 'model = "claude-opus-4-8"' in t, "model 未改"
        assert 'base_url = "http://127.0.0.1:8123/v1"' in t, "段内 base_url 未改"
        print("SELFTEST config.toml rewrite: OK")
        rpy = os.path.join(td, "relay.py")
        write_text(rpy, 'UPSTREAM = "https://relay.example.com"\nHEMA_PROXY = "http://127.0.0.1:7890"\nMODEL_OVERRIDE = os.environ.get("AGENT_ROUTER_MODEL", "deepseek-v4-flash")\n')
        P["relay_py"] = rpy
        ok, b = patch_relay("https://new.example.com", "", "claude-opus-4-8")
        t = read_text(rpy)
        assert 'UPSTREAM = "https://new.example.com"' in t
        assert 'HEMA_PROXY = ""' in t
        assert 'MODEL_OVERRIDE = os.environ.get("AGENT_ROUTER_MODEL", "claude-opus-4-8")' in t
        print("SELFTEST relay patch: OK")
        P["data_dir"] = td
        PROFILES_FILE = os.path.join(td, "profiles.json")
        d = {"方案二": {"mode": "direct", "address": "https://x.com/v1", "api_key": "k", "model": "gpt-5", "network": "direct"}}
        save_profiles(d)
        d2 = load_profiles()
        assert d2["方案二"]["model"] == "gpt-5"
        print("SELFTEST profiles: OK")
        print("SELFTEST all passed OK")
    else:
        main()