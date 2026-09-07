# -*- coding: utf-8 -*-
"""国内直连测试：ps.air-outer.com vs agentrouter.org
用法：先断开 VPN/加速器，再双击 测直连.bat
"""
import json, urllib.request, urllib.error, winreg, sys

def get_key():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
            v = winreg.QueryValueEx(k, "AGENT_ROUTER_TOKEN")[0]
            if isinstance(v, str) and v.strip():
                return v.strip()
    except Exception:
        pass
    return ""

KEY = get_key()
SPOOF = {
    "anthropic-version": "2023-06-01",
    "anthropic-dangerous-direct-browser-access": "true",
    "anthropic-beta": "claude-code-20250219",
    "x-app": "cli",
    "User-Agent": "claude-cli/2.1.31 (external, cli)",
}

def req(url, extra=None, proxy=None):
    hdrs = {"Authorization": "Bearer " + KEY, "Accept": "application/json"}
    if extra:
        hdrs.update(extra)
    r = urllib.request.Request(url, headers=hdrs)
    if proxy:
        op = urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    else:
        op = urllib.request.build_opener()
    try:
        with op.open(r, timeout=12) as resp:
            body = resp.read().decode("utf-8", "replace")
            return resp.status, body
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:200]
    except Exception as e:
        return 0, str(e)[:150]

def count_models(body):
    try:
        d = json.loads(body)
        arr = d.get("data") or d.get("models") or []
        return len(arr)
    except Exception:
        return -1

print("Key: " + (KEY[:4] + "****(长度%d)" % len(KEY) if KEY else "未找到！"))
print("=" * 50)
print("重要：请确保已断开 VPN/加速器后再看下面结果，才是大陆裸连的真实情况。")
print("=" * 50)
for host in ["https://ps.air-outer.com", "https://agentrouter.org"]:
    print("\n### " + host)
    for label, extra, proxy in [("普通直连(无伪装)", None, None),
                                ("伪装头直连", SPOOF, None),
                                ("伪装头+hema代理", SPOOF, "http://127.0.0.1:21000")]:
        code, body = req(host + "/v1/models", extra=extra, proxy=proxy)
        mc = count_models(body) if code == 200 else -1
        tag = "通,模型数=%d" % mc if code == 200 else ("失败(%s)" % code)
        print("  %-18s -> %s" % (label, tag))
print("\n把上面输出发给我即可。")
