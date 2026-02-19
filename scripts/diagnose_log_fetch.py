"""
矿机原始日志获取诊断脚本
用于排查 miner_logs 页面无法获取原始运行日志的问题
用法: python scripts/diagnose_log_fetch.py <矿机IP> [矿机IP2 ...]
       python scripts/diagnose_log_fetch.py --verbose <矿机IP>  # 详细模式，打印 HTTP 响应
示例: python scripts/diagnose_log_fetch.py 10.102.1.64 10.102.1.58
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from miners.log_fetcher import MinerLogFetcher
from database.models import get_db, init_db
import config


async def probe_http(ip: str, path: str, port: int = 80, timeout: float = 8, use_auth: bool = False) -> dict:
    """探测 HTTP 端点，返回状态码、异常、响应片段"""
    url = f"http://{ip}:{port}{path}" if port != 80 else f"http://{ip}{path}"
    result = {"url": url, "status": None, "error": None, "body_preview": None}
    try:
        import urllib.request
        from urllib.error import HTTPError
        from urllib.request import HTTPPasswordMgrWithDefaultRealm, HTTPBasicAuthHandler, build_opener
        if use_auth:
            u = config.MINER_CREDENTIALS.get("username", "root")
            p = config.MINER_CREDENTIALS.get("password", "root")
            passman = HTTPPasswordMgrWithDefaultRealm()
            passman.add_password(None, f"http://{ip}/", u, p)
            opener = build_opener(HTTPBasicAuthHandler(passman))
            req = urllib.request.Request(url)
            f = opener.open(req, timeout=timeout)
        else:
            req = urllib.request.Request(url)
            f = urllib.request.urlopen(req, timeout=timeout)
        result["status"] = f.getcode()
        body = f.read().decode("utf-8", errors="ignore")
        result["body_preview"] = body[:400] if body else ""
        f.close()
    except HTTPError as e:
        result["status"] = e.code
        result["body_preview"] = e.read().decode("utf-8", errors="ignore")[:400]
    except Exception as e:
        result["error"] = str(e)
    return result


async def probe_port(ip: str, port: int, timeout: float = 3) -> bool:
    """探测端口是否可达"""
    try:
        _, w = await asyncio.wait_for(asyncio.open_connection(ip, port), timeout=timeout)
        w.close()
        try:
            await w.wait_closed()
        except Exception:
            pass
        return True
    except Exception:
        return False


async def diagnose_one(ip: str, db, verbose: bool = False):
    """对单个矿机执行日志获取诊断"""
    from database.models import Miner
    miner = db.query(Miner).filter(Miner.ip_address == ip).first()
    if not miner:
        miner = Miner(ip_address=ip, serial_number=f"diagnose-{ip}", model="Unknown", status="unknown")
    fetcher = MinerLogFetcher(db)
    
    print(f"\n{'='*60}")
    print(f"矿机诊断: {ip}")
    print("="*60)
    
    # 0. 端口连通性
    print("\n  端口连通性:")
    for port in (4028, 80, 8080):
        ok = await probe_port(ip, port)
        print(f"    端口 {port}: {'✓ 可达' if ok else '✗ 不可达'}")
    
    # 1. 固件检测
    fw = await fetcher._detect_firmware_type(ip)
    print(f"\n  检测固件类型: {fw or '(未识别)'}")
    
    # 2. 分别测试各日志源
    print("\n  测试各日志接口:")
    tests = [
        ("原厂 log.cgi/get_kernel_log (root/root)", fetcher._fetch_stock_log(ip, 12, True)),
        ("Luxor :8080/log/live", fetcher._fetch_luxor_log(ip, 12)),
        ("JSON-RPC logs 命令", fetcher._fetch_jsonrpc_logs(ip, 12)),
        ("Braiins GraphQL + REST", fetcher._fetch_braiins_log(ip, 12)),
    ]
    for name, coro in tests:
        try:
            r = await coro
            status = f"✓ 成功 ({len(r) if r else 0} 字符)" if r and len(r) > 20 else "✗ 失败/无内容"
            print(f"    {name}: {status}")
            if r and len(r) > 20 and len(r) < 500:
                print(f"      预览: {r[:200]}...")
        except Exception as e:
            print(f"    {name}: ✗ 异常: {e}")
    
    # 3. 完整获取
    print("\n  完整 _fetch_http_log 调用:")
    system_log = await fetcher._fetch_http_log(ip, timeout=12, firmware_hint=fw)
    if system_log and len(system_log) > 20:
        print(f"  ✓ 获取到原始运行日志，共 {len(system_log)} 字符")
        print(f"  前 300 字预览:\n  ---\n  {system_log[:300]}...\n  ---")
    else:
        print("  ✗ 未能获取原始运行日志")
    
    # 4. 详细模式：探测各 HTTP 端点
    if verbose or fw in ("braiins", "bmminer"):
        print("\n  [详细] HTTP 端点探测:")
        probes = [
            ("/", 80, "根路径", False),
            ("/system/log", 80, "Braiins 系统日志", False),
            ("/graphql", 80, "Braiins GraphQL", False),
            ("/api/v1/miner/errors", 80, "Braiins 错误 API", False),
            ("/cgi-bin/get_kernel_log.cgi", 80, "原厂内核日志(无认证)", False),
            ("/log/live", 8080, "Luxor 日志", False),
        ]
        if fw in ("bmminer", "") or not fw:  # 未识别时也尝试 fix-freq 等 CGI
            for path in ["/cgi-bin/log.cgi", "/cgi-bin/get_kernel_log.cgi", "/cgi-bin/get_log.cgi", "/cgi-bin/get_miner_log.cgi", "/cgi-bin/blog.cgi", "/"]:
                probes.append((path, 80, f"bmminer(认证){path}", True))
        for path, port, desc, auth in probes:
            r = await probe_http(ip, path, port, use_auth=auth)
            status_str = f"HTTP {r['status']}" if r["status"] else f"异常: {r['error']}"
            print(f"    {desc} ({r['url']}): {status_str}")
            if r["body_preview"] and len(r["body_preview"]) > 0:
                preview = r["body_preview"][:150].replace("\n", " ")
                print(f"      响应预览: {preview}...")
        
        # Braiins GraphQL 原始响应调试（保存到文件便于排查）
        if fw == "braiins" and verbose:
            print("\n  [详细] Braiins GraphQL 原始响应:")
            try:
                from http.cookiejar import CookieJar
                import urllib.request
                cookie_jar = CookieJar()
                opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))
                opener.open(urllib.request.Request(f"http://{ip}/system/log", headers={"User-Agent": "Mozilla/5.0"}), timeout=10)
                q = getattr(fetcher, "_BRAIINS_GRAPHQL_LOGS", "query SystemGetLogs { bos { errors: errors { ... on ErrorEntry { timestamp message } } bosminer: log(target: BOSMINER) syslog: log(target: SYSLOG) } }")
                req = urllib.request.Request(
                    f"http://{ip}/graphql",
                    data=json.dumps({"operationName": "SystemGetLogs", "query": q, "variables": {}}).encode(),
                    headers={"Content-Type": "application/json", "Accept": "application/json", "Referer": f"http://{ip}/system/log", "User-Agent": "Mozilla/5.0"},
                    method="POST"
                )
                with opener.open(req, timeout=15) as f:
                    raw = f.read().decode("utf-8", errors="ignore")
                data = json.loads(raw)
                bos = (data.get("data") or {}).get("bos") or {}
                for k in ("errors", "bosminer", "syslog"):
                    v = bos.get(k)
                    t = "list" if isinstance(v, list) else type(v).__name__
                    n = len(v) if isinstance(v, (list, str)) else 0
                    print(f"    {k}: type={t}, len={n}")
                dump_path = Path(__file__).parent.parent / "data" / f"braiins_graphql_{ip.replace('.','_')}.json"
                dump_path.parent.mkdir(parents=True, exist_ok=True)
                with open(dump_path, "w", encoding="utf-8") as f:
                    f.write(raw)
                print(f"    已保存到: {dump_path}")
            except Exception as e:
                print(f"    GraphQL 调试失败: {e}")

        # Braiins 登录 + miner/errors 完整测试
        if fw == "braiins":
            print("\n  [详细] Braiins 登录 + miner/errors 测试:")
            for pwd in ("root", ""):
                try:
                    import urllib.request
                    req = urllib.request.Request(
                        f"http://{ip}/api/v1/auth/login",
                        data=json.dumps({"username": "root", "password": pwd}).encode(),
                        headers={"Content-Type": "application/json"},
                        method="POST"
                    )
                    with urllib.request.urlopen(req, timeout=8) as f:
                        j = json.loads(f.read().decode())
                        tok = j.get("token", "")
                        print(f"    密码 '{pwd or '(空)'}': ✓ 登录成功, token 长度={len(tok)}")
                        if tok:
                            for auth_hdr in [f"Token {tok}", tok]:
                                try:
                                    req2 = urllib.request.Request(
                                        f"http://{ip}/api/v1/miner/errors",
                                        headers={"Authorization": auth_hdr}
                                    )
                                    with urllib.request.urlopen(req2, timeout=8) as f2:
                                        data = json.loads(f2.read().decode())
                                        errs = data.get("errors", [])
                                        print(f"    miner/errors (Auth: {'Token' if 'Token' in auth_hdr else '裸token'}): ✓ 成功, {len(errs)} 条")
                                        if errs:
                                            print(f"      示例: {errs[0]}")
                                        break
                                except Exception as e2:
                                    print(f"    miner/errors: ✗ {e2}")
                                    continue
                        break
                except Exception as e:
                    print(f"    密码 '{pwd or '(空)'}': ✗ {e}")


async def main():
    init_db()
    args = sys.argv[1:] if len(sys.argv) > 1 else []
    verbose = "--verbose" in args or "-v" in args
    ips = [a for a in args if not a.startswith("-")]
    if not ips:
        print("用法: python scripts/diagnose_log_fetch.py [--verbose] <矿机IP> [IP2 ...]")
        print("示例: python scripts/diagnose_log_fetch.py 10.102.1.64")
        print("       python scripts/diagnose_log_fetch.py --verbose 10.102.1.64  # 详细模式")
        sys.exit(1)
    
    db = next(get_db())
    try:
        for ip in ips:
            await diagnose_one(ip.strip(), db, verbose=verbose)
    finally:
        db.close()
    
    print("\n" + "="*60)
    print("排查提示:")
    print("  1. 若端口 80 不可达：Braiins Web 界面无法访问，请检查矿机网络/防火墙")
    print("  2. 若 HTTP 端点返回 401/403：可能需要登录，尝试 config.py 中设置 root/root 或空密码")
    print("  3. Braiins 日志接口：若 /system/log 返回 404，请用浏览器打开 http://矿机IP")
    print("     按 F12 -> Network，刷新后查看加载日志时实际请求的 URL，反馈给开发")
    print("  4. 原厂 fix-freq：需 config.py 中 MINER_CREDENTIALS 为 root/root")
    print("="*60)


if __name__ == "__main__":
    asyncio.run(main())
