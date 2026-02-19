"""
矿机日志获取器 - 优先从矿机 HTTP 接口获取与后台一致的系统日志
支持: 原厂 get_kernel_log.cgi, Luxor :8080/log/live, Braiins /api/v1/miner/errors, JSON-RPC logs 命令
"""
import asyncio
import json
import time
import re
from datetime import datetime
from typing import List, Dict, Optional

from sqlalchemy.orm import Session
from database.models import Miner, MinerLog

import config


class MinerLogFetcher:
    """矿机日志获取器"""
    
    def __init__(self, db: Session):
        self.db = db
    
    async def _send_command(self, ip: str, command: str, port: int = 4028, timeout: float = 10,
                            require_status: bool = True) -> Optional[Dict]:
        """发送 JSON-RPC 命令"""
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(ip, port),
                timeout=timeout
            )
            
            cmd = json.dumps({"command": command}) + "\n"
            writer.write(cmd.encode())
            await writer.drain()
            
            response_data = b""
            start_time = time.time()
            
            try:
                while True:
                    chunk = await asyncio.wait_for(reader.read(8192), timeout=3.0)
                    if not chunk:
                        break
                    response_data += chunk
                    if time.time() - start_time > timeout:
                        break
                    text = response_data.decode('utf-8', errors='ignore')
                    if text.strip().endswith('}'):
                        break
            except asyncio.TimeoutError:
                pass
            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass
            
            if not response_data:
                return None
            
            text = response_data.decode('utf-8', errors='ignore').strip()
            json_start = text.find('{')
            json_end = text.rfind('}') + 1
            
            if json_start < 0 or json_end <= json_start:
                return None
            
            data = json.loads(text[json_start:json_end])
            if require_status:
                if 'STATUS' not in data or not data['STATUS']:
                    return None
                status = data['STATUS'][0]
                if status.get('STATUS') != 'S':
                    return None
            return data
        except Exception as e:
            print(f"[ERROR] 日志获取命令失败 {ip}:{command} - {e}")
            return None
    
    async def _fetch_jsonrpc_logs(self, ip: str, timeout: float = 12) -> Optional[str]:
        """JSON-RPC logs 命令：Luxor LUXminer 及部分 BOSminer 衍生固件支持"""
        for cmd in ("logs", "log"):
            try:
                data = await self._send_command(ip, cmd, port=4028, timeout=timeout, require_status=False)
                if not data:
                    continue
                # 解析各种可能的响应格式
                lines = []
                if "LOG" in data and isinstance(data["LOG"], list):
                    for x in data["LOG"]:
                        if isinstance(x, str):
                            lines.append(x)
                        elif isinstance(x, dict) and "log" in x:
                            lines.append(x["log"])
                elif "log" in data:
                    val = data["log"]
                    if isinstance(val, str):
                        lines = [ln.strip() for ln in val.split("\n") if ln.strip()]
                    elif isinstance(val, list):
                        lines = [str(x) for x in val if x]
                elif "LOGS" in data and isinstance(data["LOGS"], list):
                    for x in data["LOGS"]:
                        lines.append(str(x) if isinstance(x, str) else x.get("message", str(x)))
                elif "Description" in data.get("STATUS", [{}])[0]:
                    desc = data["STATUS"][0].get("Description", "")
                    if "Log" in desc or len(desc) > 100:
                        lines = [ln.strip() for ln in desc.split("\n") if ln.strip()]
                if lines:
                    out = "\n".join(lines)
                    if len(out) > 80:
                        print(f"[LOG] 成功从 JSON-RPC {cmd} 获取日志: {ip}")
                        return out
            except Exception as e:
                pass
        return None

    def _extract_log_from_html(self, html: str) -> Optional[str]:
        """从 HTML 页面中提取日志内容（Antminer/fix-freq 等固件的日志页可能用 HTML 包装）"""
        if not html or len(html) < 30:
            return None
        kw_log = ('ERROR', 'WARN', 'kernel', 'miner', 'chip', 'board', 'pool', 'log', 'stratum', 'INFO')

        def _clean(txt: str) -> str:
            txt = txt.replace('&nbsp;', ' ').replace('&lt;', '<').replace('&gt;', '>').replace('&amp;', '&')
            return txt.strip()

        # 1. 优先 <pre>、<textarea>、<code>
        for pat in [r'<pre[^>]*>([\s\S]*?)</pre>', r'<textarea[^>]*>([\s\S]*?)</textarea>', r'<code[^>]*>([\s\S]*?)</code>']:
            m = re.search(pat, html, re.I)
            if m:
                txt = _clean(m.group(1))
                if len(txt) > 80 and any(k in txt for k in kw_log):
                    return txt
        # 2. id/class 含 log、blog 的 div
        for pat in [r'<div[^>]*(?:id|class)="[^"]*blog[^"]*"[^>]*>([\s\S]*?)</div>',
                    r'<div[^>]*(?:id|class)="[^"]*log[^"]*"[^>]*>([\s\S]*?)</div>']:
            m = re.search(pat, html, re.I)
            if m:
                txt = re.sub(r'<[^>]+>', '\n', m.group(1))
                txt = _clean(txt)
                if len(txt) > 80 and any(k in txt for k in kw_log):
                    return txt
        return None

    async def _fetch_stock_log(self, ip: str, timeout: float = 15, use_auth: bool = True) -> Optional[str]:
        """原厂/衍生固件 fix-freq(bmminer): get_kernel_log.cgi 等 (端口 80)，需 root/root 认证"""
        from urllib.request import Request, build_opener, urlopen
        
        user = config.MINER_CREDENTIALS.get("username", "root")
        pwd = config.MINER_CREDENTIALS.get("password", "root")
        # 多种 CGI 路径：fix-freq 使用 log.cgi，原厂使用 get_kernel_log 等
        cgi_paths = [
            "/cgi-bin/log.cgi",           # fix-freq #blog 对应接口
            "/cgi-bin/get_kernel_log.cgi",
            "/cgi-bin/get_log.cgi",
            "/cgi-bin/get_system_log.cgi",
            "/cgi-bin/get_miner_log.cgi",
            "/cgi-bin/blog.cgi",
            "/cgi-bin/get_blog.cgi",
        ]
        
        def _sync():
            try:
                if use_auth:
                    from urllib.request import HTTPPasswordMgrWithDefaultRealm, HTTPBasicAuthHandler, HTTPDigestAuthHandler
                    passman = HTTPPasswordMgrWithDefaultRealm()
                    passman.add_password(None, f"http://{ip}/", user, pwd)
                    opener = build_opener(HTTPBasicAuthHandler(passman), HTTPDigestAuthHandler(passman))
                else:
                    opener = None

                # 优先从 CGI 获取完整日志（log.cgi 等返回全量），避免 HTML 提取只拿到当日片段
                for path in cgi_paths:
                    url = f"http://{ip}{path}"
                    try:
                        if opener:
                            req = Request(url)
                            with opener.open(req, timeout=timeout) as f:
                                t = f.read().decode("utf-8", errors="ignore").strip()
                        else:
                            with urlopen(url, timeout=timeout) as f:
                                t = f.read().decode("utf-8", errors="ignore").strip()
                    except Exception:
                        continue
                    if not t or len(t) < 30:
                        continue
                    # 纯文本直接返回
                    if "<html" not in t.lower() and "<!doctype" not in t.lower():
                        if len(t) > 50:
                            return t
                        continue
                    # HTML 包装的日志尝试提取
                    extracted = self._extract_log_from_html(t)
                    if extracted:
                        return extracted
                # CGI 均失败时，回退到首页 HTML 提取（部分固件可能只在此提供日志）
                if use_auth and opener:
                    for page_path in ("/", "/index.html"):
                        try:
                            url = f"http://{ip}{page_path}"
                            req = Request(url)
                            with opener.open(req, timeout=timeout) as f:
                                t = f.read().decode("utf-8", errors="ignore").strip()
                            if t and len(t) > 200:
                                ex = self._extract_log_from_html(t)
                                if ex:
                                    return ex
                        except Exception:
                            pass
            except Exception:
                pass
            return None
        
        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, _sync)
        except Exception:
            return None

    async def _fetch_luxor_log(self, ip: str, timeout: float = 15) -> Optional[str]:
        """Luxor LUXminer: GET :8080/log/live (端口 8080，无需认证)"""
        def _sync():
            try:
                import urllib.request
                lines = []
                for page in range(30):  # 30 页 × 1000 条，尽量拉取完整历史日志
                    url = f"http://{ip}:8080/log/live?page={page}&page_size=1000"
                    try:
                        with urllib.request.urlopen(url, timeout=timeout) as f:
                            data = json.loads(f.read().decode("utf-8", errors="ignore"))
                    except Exception:
                        break
                    entries = data.get("data", [])
                    if not entries:
                        break
                    for e in entries:
                        ts = e.get("timestamp") or (e.get("fields") or {}).get("timestamp", "")
                        msg = e.get("message") or (e.get("fields") or {}).get("message", "")
                        lvl = e.get("level", "")
                        if msg:
                            lines.append(f"{ts} [{lvl}] {msg}" if lvl else f"{ts} {msg}")
                return "\n".join(lines) if lines else None
            except Exception as e:
                print(f"[LOG] Luxor 日志获取失败 {ip}:8080: {e}")
            return None

        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, _sync)
        except Exception:
            return None

    async def _fetch_braiins_errors_no_auth(self, ip: str, timeout: float = 15) -> Optional[str]:
        """Braiins OS: 部分版本 /api/v1/miner/errors 允许无认证访问"""
        def _sync():
            try:
                import urllib.request
                with urllib.request.urlopen(f"http://{ip}/api/v1/miner/errors", timeout=timeout) as f:
                    data = json.loads(f.read().decode("utf-8", errors="ignore"))
                errs = data.get("errors", [])
                if not errs:
                    return None
                lines = [f"{e.get('timestamp','')} {e.get('message','')}" for e in errs]
                return "\n".join(lines) if len("\n".join(lines)) > 50 else None
            except Exception:
                return None
        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, _sync)
        except Exception:
            return None

    async def _fetch_braiins_system_log(self, ip: str, timeout: float = 15) -> Optional[str]:
        """Braiins OS: /system/log 页面（与矿机后台 http://IP/system/log 一致，部分版本无需登录）"""
        def _try_no_auth():
            for path in ("/system/log", "/system/logs", "/logs", "/log"):
                try:
                    import urllib.request
                    with urllib.request.urlopen(f"http://{ip}{path}", timeout=timeout) as f:
                        return f.read().decode("utf-8", errors="ignore").strip()
                except Exception:
                    pass
            return None

        def _extract_log_from_body(body: str) -> Optional[str]:
            """从 HTML 或纯文本中提取日志内容（Braiins /system/log 等 SPA 可能嵌 JSON）"""
            if not body or len(body) < 30:
                return None
            import re
            if "<" in body and ">" in body:
                # 1. 提取 <script type="application/json"> 或 id="__NEXT_DATA__" 等中的 JSON
                for script_pat in [
                    r'<script[^>]*id="__NEXT_DATA__"[^>]*>([\s\S]*?)</script>',
                    r'<script[^>]*type="application/json"[^>]*>([\s\S]*?)</script>',
                    r'window\.__(?:PRELOADED|INITIAL)_STATE__\s*=\s*(\{[\s\S]*?\});',
                    r'"logs"\s*:\s*(\[[\s\S]*?\])',
                    r'"entries"\s*:\s*(\[[\s\S]*?\])',
                ]:
                    m = re.search(script_pat, body, re.I)
                    if m:
                        try:
                            jstr = m.group(1).strip()
                            j = json.loads(jstr)
                            # 递归查找 logs/entries/messages 数组
                            def find_logs(obj, depth=0):
                                if depth > 5:
                                    return None
                                if isinstance(obj, list) and len(obj) > 2:
                                    first = obj[0] if obj else {}
                                    if isinstance(first, dict) and any(k in first for k in ('message', 'timestamp', 'level')):
                                        return '\n'.join(
                                            f"{x.get('timestamp','')} [{x.get('level','')}] {x.get('message','')}"
                                            for x in obj if isinstance(x, dict)
                                        )
                                if isinstance(obj, dict):
                                    for k in ('logs', 'entries', 'messages', 'log', 'data'):
                                        if k in obj:
                                            r = find_logs(obj[k], depth + 1)
                                            if r and len(r) > 80:
                                                return r
                                return None
                            out = find_logs(j)
                            if out and len(out) > 80:
                                return out
                        except (json.JSONDecodeError, ValueError):
                            pass
                # 2. 传统 HTML 元素
                for pat in [r'<pre[^>]*>([\s\S]*?)</pre>', r'<code[^>]*>([\s\S]*?)</code>',
                            r'<main[^>]*>([\s\S]*?)</main>', r'<article[^>]*>([\s\S]*?)</article>',
                            r'<body[^>]*>([\s\S]*?)</body>',
                            r'class="[^"]*log[^"]*"[\s\S]*?>([\s\S]*?)</',
                            r'id="[^"]*log[^"]*"[\s\S]*?>([\s\S]*?)</']:
                    m = re.search(pat, body, re.I)
                    if m and len(m.group(1).strip()) > 50:
                        txt = m.group(1).strip()
                        txt = re.sub(r'<[^>]+>', '', txt)
                        txt = txt.replace('&nbsp;', ' ').replace('&lt;', '<').replace('&gt;', '>').replace('&amp;', '&')
                        if len(txt) > 50:
                            return txt
                text = re.sub(r'<script[\s\S]*?</script>', '', body, flags=re.I)
                text = re.sub(r'<style[\s\S]*?</style>', '', text, flags=re.I)
                text = re.sub(r'<[^>]+>', '\n', text)
                text = text.replace('&nbsp;', ' ').replace('&lt;', '<').replace('&gt;', '>').replace('&amp;', '&')
                lines = [ln.strip() for ln in text.split('\n') if len(ln.strip()) > 15]
                if lines:
                    return '\n'.join(lines)
                log_lines = re.findall(r'^\d{4}-\d{2}-\d{2}T[\d.:]+Z\s+(?:ERROR|WARN|INFO|DEBUG)\s+.+', body, re.M)
                if log_lines:
                    return '\n'.join(log_lines)
            else:
                return body.strip()
            return None

        try:
            loop = asyncio.get_running_loop()
            body = await loop.run_in_executor(None, _try_no_auth)
            if body:
                out = _extract_log_from_body(body)
                if out and len(out) > 50:
                    return out
        except Exception:
            pass
        # 需要 token 认证
        user = config.MINER_CREDENTIALS.get("username", "root")
        pwd = config.MINER_CREDENTIALS.get("password", "root")
        
        async def _async_fetch():
            try:
                import aiohttp
                async with aiohttp.ClientSession() as session:
                    # 1. 登录获取 token
                    login_url = f"http://{ip}/api/v1/auth/login"
                    async with session.post(login_url, json={"username": user, "password": pwd}, timeout=aiohttp.ClientTimeout(total=timeout)) as r:
                        if r.status != 200:
                            return None
                        tok = (await r.json()).get("token")
                        if not tok:
                            return None
                    # 2. 获取 /system/log（与后台 http://IP/system/log 页面一致）
                    headers = {"Authorization": f"Token {tok}", "Accept": "text/html,application/json,text/plain,*/*"}
                    for url in (f"http://{ip}/system/log", f"http://{ip}/api/v1/system/log", f"http://{ip}/system/log/raw"):
                        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=timeout)) as r:
                            if r.status != 200:
                                continue
                            body = await r.text()
                            if not body or len(body) < 50:
                                continue
                            # JSON 响应（如 /api/v1/system/log）
                            if body.strip().startswith('{'):
                                try:
                                    j = json.loads(body)
                                    for key in ('log', 'logs', 'content', 'data', 'message'):
                                        if key in j and j[key]:
                                            val = j[key]
                                            txt = val if isinstance(val, str) else '\n'.join(str(x) for x in val)
                                            if len(txt) > 80:
                                                return txt
                                except json.JSONDecodeError:
                                    pass
                            import re
                            if "<" in body and ">" in body:
                                for pat in [r'<pre[^>]*>([\s\S]*?)</pre>', r'<code[^>]*>([\s\S]*?)</code>',
                                            r'class="[^"]*log[^"]*"[\s\S]*?>([\s\S]*?)</',
                                            r'id="[^"]*log[^"]*"[\s\S]*?>([\s\S]*?)</']:
                                    m = re.search(pat, body, re.I)
                                    if m and len(m.group(1).strip()) > 100:
                                        txt = m.group(1).strip()
                                        txt = re.sub(r'<[^>]+>', '', txt)
                                        txt = txt.replace('&nbsp;', ' ').replace('&lt;', '<').replace('&gt;', '>').replace('&amp;', '&')
                                        if len(txt) > 50:
                                            return txt
                                text = re.sub(r'<script[\s\S]*?</script>', '', body, flags=re.I)
                                text = re.sub(r'<style[\s\S]*?</style>', '', text, flags=re.I)
                                text = re.sub(r'<[^>]+>', '\n', text)
                                text = text.replace('&nbsp;', ' ').replace('&lt;', '<').replace('&gt;', '>').replace('&amp;', '&')
                                lines = [ln.strip() for ln in text.split('\n') if len(ln.strip()) > 10]
                                if lines:
                                    return '\n'.join(lines)
                            else:
                                return body.strip()
                    return None
            except Exception as e:
                print(f"[LOG] Braiins /system/log 获取失败 {ip}: {e}")
            return None
        
        try:
            return await _async_fetch()
        except Exception:
            return None

    # Braiins GraphQL SystemGetLogs 查询（与后台 /system/log 一致）
    _BRAIINS_GRAPHQL_LOGS = """query SystemGetLogs {
  bos {
    errors: errors {
      ... on ErrorEntry {
        timestamp
        errorCodes { code hint reason }
        components { name index }
        message
      }
    }
    bosminer: log(target: BOSMINER)
    monitor: log(target: MONITOR)
    syslog: log(target: SYSLOG)
    boser: log(target: BOSER)
    dmesg: log(target: DMESG)
  }
}
"""

    async def _fetch_braiins_graphql_logs(self, ip: str, timeout: float = 15) -> Optional[str]:
        """Braiins OS: GraphQL SystemGetLogs 获取完整日志（errors+bosminer+monitor+syslog+boser+dmesg）"""
        def _sync():
            try:
                import urllib.request
                from http.cookiejar import CookieJar
                # 获取 session：先访问页面，再尝试 REST 登录（部分版本需登录才返回完整日志）
                cookie_jar = CookieJar()
                opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))
                browser_headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                }
                opener.open(urllib.request.Request(f"http://{ip}/system/log", headers=browser_headers), timeout=timeout)
                # REST 登录可能设置 session cookie，用于 GraphQL
                for try_pwd in (config.MINER_CREDENTIALS.get("password", "root"), ""):
                    try:
                        req_login = urllib.request.Request(
                            f"http://{ip}/api/v1/auth/login",
                            data=json.dumps({"username": "root", "password": try_pwd}).encode(),
                            headers={"Content-Type": "application/json", **browser_headers},
                            method="POST"
                        )
                        opener.open(req_login, timeout=timeout)
                        break
                    except Exception:
                        pass
                # POST GraphQL（与浏览器请求一致）
                req = urllib.request.Request(
                    f"http://{ip}/graphql",
                    data=json.dumps({
                        "operationName": "SystemGetLogs",
                        "query": self._BRAIINS_GRAPHQL_LOGS,
                        "variables": {}
                    }).encode(),
                    headers={
                        "Content-Type": "application/json",
                        "Accept": "application/graphql-response+json, application/json",
                        "Referer": f"http://{ip}/system/log",
                        "Origin": f"http://{ip}",
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
                    },
                    method="POST"
                )
                with opener.open(req, timeout=timeout) as f:
                    data = json.loads(f.read().decode("utf-8", errors="ignore"))
            except Exception as e:
                print(f"[LOG] Braiins GraphQL 失败 {ip}: {e}")
                return None
            if data.get("errors"):
                return None
            bos = (data.get("data") or {}).get("bos")
            if not bos:
                return None

            def _to_text(val):
                """GraphQL 返回 log 为字符串数组或单字符串"""
                if not val:
                    return ""
                if isinstance(val, list):
                    return "\n".join(str(x).strip() for x in val if x)
                return str(val).strip() if isinstance(val, str) else ""

            # 合并所有日志源，便于故障分析：errors > bosminer > monitor > syslog > boser > dmesg
            sections = []
            errs = bos.get("errors") or []
            if errs:
                lines = [f"{e.get('timestamp','')} [ERROR] {e.get('message','')}" for e in errs if isinstance(e, dict)]
                if lines:
                    sections.append(("errors（错误）", "\n".join(lines)))
            for name, key, desc in [
                ("bosminer", "bosminer", "挖矿核心日志"),
                ("monitor", "monitor", "监控/温度/算力"),
                ("syslog", "syslog", "系统日志"),
                ("boser", "boser", "Braiins OS"),
                ("dmesg", "dmesg", "内核日志"),
            ]:
                txt = _to_text(bos.get(key))
                if txt and len(txt) > 5:
                    sections.append((f"{name}（{desc}）", txt))
            if not sections:
                return None
            return "\n\n".join(f"========== {title} ==========\n{body}" for title, body in sections)

        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, _sync)
        except Exception:
            return None

    async def _fetch_braiins_rest_status(self, ip: str, timeout: float = 15) -> Optional[str]:
        """Braiins OS: REST API 获取算力、温度、ASIC 板健康（需登录）"""
        def _sync():
            try:
                import urllib.request
                user = config.MINER_CREDENTIALS.get("username", "root")
                pwd = config.MINER_CREDENTIALS.get("password", "root")
                for try_pwd in (pwd, "", "root"):
                    try:
                        req = urllib.request.Request(
                            f"http://{ip}/api/v1/auth/login",
                            data=json.dumps({"username": user, "password": try_pwd}).encode(),
                            headers={"Content-Type": "application/json"},
                            method="POST"
                        )
                        with urllib.request.urlopen(req, timeout=timeout) as f:
                            tok = json.loads(f.read().decode()).get("token")
                        if not tok:
                            continue
                        auth = f"Token {tok}"
                        lines = ["========== Braiins 矿机状态（算力/温度/ASIC） =========="]
                        for url, key in [
                            (f"http://{ip}/api/v1/miner/stats", "stats"),
                            (f"http://{ip}/api/v1/miner/hw/hashboards", "hashboards"),
                            (f"http://{ip}/api/v1/cooling/state", "cooling"),
                        ]:
                            try:
                                r = urllib.request.Request(url, headers={"Authorization": auth})
                                with urllib.request.urlopen(r, timeout=timeout) as f:
                                    data = json.loads(f.read().decode("utf-8", errors="ignore"))
                                lines.append(json.dumps(data, ensure_ascii=False, indent=2))
                                lines.append("")
                            except Exception:
                                pass
                        out = "\n".join(lines).strip()
                        return out if len(out) > 50 else None
                    except Exception:
                        continue
            except Exception as e:
                print(f"[LOG] Braiins REST 状态获取失败 {ip}: {e}")
            return None

        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, _sync)
        except Exception:
            return None

    def _prepend_braiins_status(self, status_block: Optional[str], log_content: str) -> str:
        """将 REST 状态块置于日志内容前"""
        if status_block and log_content:
            return status_block + "\n\n" + log_content
        return status_block or log_content

    async def _fetch_braiins_log(self, ip: str, timeout: float = 15) -> Optional[str]:
        """Braiins OS: REST 状态（算力/温度/ASIC）+ GraphQL 完整日志（合并所有日志源）"""
        status_block = await self._fetch_braiins_rest_status(ip, timeout)
        result = await self._fetch_braiins_graphql_logs(ip, timeout)
        if result and len(result) > 50:
            return self._prepend_braiins_status(status_block, result)
        result = await self._fetch_braiins_system_log(ip, timeout)
        if result and len(result) > 50:
            return self._prepend_braiins_status(status_block, result)
        result = await self._fetch_braiins_errors_no_auth(ip, timeout)
        if result:
            return self._prepend_braiins_status(status_block, result)
        # miner/errors 需登录（Braiins 默认 root/root）
        user = config.MINER_CREDENTIALS.get("username", "root")
        pwd = config.MINER_CREDENTIALS.get("password", "root")

        def _sync_braiins_errors() -> Optional[str]:
            """同步实现，作为 aiohttp 的 fallback"""
            import urllib.request
            for try_pwd in (pwd, "", "root", "braiins"):
                try:
                    req = urllib.request.Request(
                        f"http://{ip}/api/v1/auth/login",
                        data=json.dumps({"username": user, "password": try_pwd}).encode(),
                        headers={"Content-Type": "application/json"},
                        method="POST"
                    )
                    with urllib.request.urlopen(req, timeout=timeout) as f:
                        j = json.loads(f.read().decode())
                    tok = j.get("token", "")
                    if not tok:
                        continue
                    for auth_val in (tok, f"Token {tok}"):  # Braiins 优先裸 token
                        try:
                            req2 = urllib.request.Request(
                                f"http://{ip}/api/v1/miner/errors",
                                headers={"Authorization": auth_val}
                            )
                            with urllib.request.urlopen(req2, timeout=timeout) as f2:
                                data = json.loads(f2.read().decode())
                            errs = data.get("errors", [])
                            if not errs:
                                return "[Braiins OS] 暂无错误记录（API 连通正常）"
                            return "\n".join(f"{e.get('timestamp','')} {e.get('message','')}" for e in errs)
                        except Exception:
                            continue
                except Exception:
                    continue
            return None

        err_result = None
        try:
            import aiohttp
            auth_formats = [lambda t: t, lambda t: f"Token {t}", lambda t: f"Bearer {t}"]  # Braiins 优先裸 token
            for try_pwd in (pwd, "", "root", "braiins"):
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.post(
                            f"http://{ip}/api/v1/auth/login",
                            json={"username": user, "password": try_pwd},
                            timeout=aiohttp.ClientTimeout(total=timeout)
                        ) as r:
                            if r.status != 200:
                                continue
                            j = await r.json()
                            tok = j.get("token") or (str(j.get("authorization", "")).replace("Token ", "").replace("Bearer ", ""))
                            if not tok:
                                continue
                        for fmt in auth_formats:
                            try:
                                async with session.get(
                                    f"http://{ip}/api/v1/miner/errors",
                                    headers={"Authorization": fmt(tok)},
                                    timeout=aiohttp.ClientTimeout(total=timeout)
                                ) as r:
                                    if r.status != 200:
                                        continue
                                    data = await r.json()
                                    errs = data.get("errors", [])
                                    if not errs:
                                        err_result = "[Braiins OS] 暂无错误记录（API 连通正常）"
                                    else:
                                        err_result = "\n".join(f"{e.get('timestamp','')} {e.get('message','')}" for e in errs)
                                    break
                            except Exception:
                                continue
                        if err_result:
                            break
                except Exception as e:
                    print(f"[LOG] Braiins aiohttp 失败 {ip}: {e}")
                    continue
        except ImportError:
            pass
        if not err_result:
            try:
                loop = asyncio.get_running_loop()
                err_result = await loop.run_in_executor(None, _sync_braiins_errors)
            except Exception:
                err_result = _sync_braiins_errors()
        if err_result:
            return self._prepend_braiins_status(status_block, err_result)
        return status_block if status_block else None

    async def _detect_firmware_type(self, ip: str) -> Optional[str]:
        """根据 JSON-RPC summary 推断固件类型：bmminer(fix-freq)、luxor、braiins"""
        try:
            data = await self._send_command(ip, "summary", port=4028, timeout=8, require_status=False)
            if not data:
                return None
            raw = json.dumps(data).upper()
            if "LUXMINER" in raw or "LUXOR" in raw:
                return "luxor"
            if "BOSER" in raw or "BRAIINS" in raw or "BOSMINER" in raw:
                return "braiins"
            if "BMMINER" in raw or "ANTA" in raw or "ANTMINER" in raw or "Type" in raw or "FIX-FREQ" in raw or "FIX_FREQ" in raw:
                return "bmminer"
            return None
        except Exception:
            return None

    async def _fetch_http_log(self, ip: str, path: str = None, timeout: float = 15,
                              firmware_hint: Optional[str] = None) -> Optional[str]:
        """
        按固件类型优先尝试对应日志接口：
        1) bmminer(fix-freq): 原厂 get_kernel_log.cgi (root/root)
        2) Luxor LUXminer: :8080/log/live 或 JSON-RPC logs
        3) Braiins OS: /system/log 或 /api/v1/miner/errors
        """
        fw = firmware_hint or await self._detect_firmware_type(ip)
        # 按固件排序尝试顺序
        if fw == "bmminer":
            attempts = [
                ("原厂(认证)", lambda: self._fetch_stock_log(ip, timeout, use_auth=True)),
                ("原厂(无认证)", lambda: self._fetch_stock_log(ip, timeout, use_auth=False)),
                ("JSON-RPC logs", lambda: self._fetch_jsonrpc_logs(ip, timeout)),
                ("Luxor", lambda: self._fetch_luxor_log(ip, timeout)),
                ("Braiins", lambda: self._fetch_braiins_log(ip, timeout)),
            ]
        elif fw == "luxor":
            attempts = [
                ("Luxor", lambda: self._fetch_luxor_log(ip, timeout)),
                ("JSON-RPC logs", lambda: self._fetch_jsonrpc_logs(ip, timeout)),
                ("Braiins", lambda: self._fetch_braiins_log(ip, timeout)),
                ("原厂(认证)", lambda: self._fetch_stock_log(ip, timeout, use_auth=True)),
                ("原厂(无认证)", lambda: self._fetch_stock_log(ip, timeout, use_auth=False)),
            ]
        elif fw == "braiins":
            attempts = [
                ("Braiins", lambda: self._fetch_braiins_log(ip, timeout)),
                ("JSON-RPC logs", lambda: self._fetch_jsonrpc_logs(ip, timeout)),
                ("Luxor", lambda: self._fetch_luxor_log(ip, timeout)),
                ("原厂(认证)", lambda: self._fetch_stock_log(ip, timeout, use_auth=True)),
                ("原厂(无认证)", lambda: self._fetch_stock_log(ip, timeout, use_auth=False)),
            ]
        else:
            # 无法识别时先尝试原厂/fix-freq（多数为 bmminer 衍生）
            attempts = [
                ("原厂(认证)", lambda: self._fetch_stock_log(ip, timeout, use_auth=True)),
                ("原厂(无认证)", lambda: self._fetch_stock_log(ip, timeout, use_auth=False)),
                ("Luxor", lambda: self._fetch_luxor_log(ip, timeout)),
                ("Braiins", lambda: self._fetch_braiins_log(ip, timeout)),
                ("JSON-RPC logs", lambda: self._fetch_jsonrpc_logs(ip, timeout)),
            ]
        best_result = None
        best_len = 0
        for name, fetch_fn in attempts:
            try:
                result = await fetch_fn()
                if result and len(result) > 20:
                    if len(result) > best_len:
                        best_result = result
                        best_len = len(result)
                    # 若首次成功但内容较短（<80KB），继续尝试其他源以获取更完整日志
                    if len(result) >= 80000:
                        break
            except Exception as e:
                print(f"[LOG] {name} 日志尝试失败 {ip}: {e}")
        if best_result:
            print(f"[LOG] 成功获取日志: {ip}，共 {best_len} 字符")
            return best_result
        return None
    
    def _parse_log_type(self, log_line: str) -> str:
        """解析日志类型"""
        log_upper = log_line.upper()
        if 'ERROR' in log_upper or 'FAIL' in log_upper:
            return 'error'
        elif 'WARN' in log_upper:
            return 'warning'
        elif 'INFO' in log_upper:
            return 'info'
        else:
            return 'system'
    
    def _analyze_log_line(self, log_line: str) -> Optional[str]:
        """分析日志行，提取故障关键信息"""
        log_upper = log_line.upper()
        
        # 检测无算力相关问题
        if 'NO HASHRATE' in log_upper or 'HASHRATE 0' in log_upper:
            return '检测到无算力: 可能是算力板故障或连接问题'
        
        if 'CHIP' in log_upper and ('ERROR' in log_upper or 'FAIL' in log_upper):
            return '芯片错误: 算力板可能损坏或需要重新初始化'
        
        if 'ASIC' in log_upper and ('NOT' in log_upper or 'FAIL' in log_upper):
            return 'ASIC 故障: 算力芯片无响应'
        
        if 'TEMP' in log_upper and ('HIGH' in log_upper or 'OVER' in log_upper):
            return '温度过高: 可能导致算力板停止工作'
        
        if 'FAN' in log_upper and ('ERROR' in log_upper or 'STOP' in log_upper):
            return '风扇故障: 散热不足导致保护性降频或停机'
        
        if 'POWER' in log_upper and ('ERROR' in log_upper or 'FAIL' in log_upper):
            return '电源故障: 供电不稳定影响算力'
        
        if 'POOL' in log_upper and ('STRATUM' in log_upper or 'CONNECT' in log_upper) and 'FAIL' in log_upper:
            return '矿池连接失败: 网络问题或矿池地址错误'
        
        if 'VOLTAGE' in log_upper and ('LOW' in log_upper or 'ERROR' in log_upper):
            return '电压异常: 算力板供电不足'
        
        if 'FREQUENCY' in log_upper and 'ERROR' in log_upper:
            return '频率设置错误: 可能需要重新配置'
        
        if 'RESTART' in log_upper or 'REBOOT' in log_upper:
            return '矿机重启: 检查是否频繁重启导致算力不稳定'
        
        return None
    
    async def fetch_detailed_logs(self, miner: Miner) -> Dict:
        """获取矿机详细原始日志（包含矿池和算力板信息）"""
        if not miner.ip_address:
            return {
                'pools': [],
                'boards': [],
                'raw_logs': []
            }
        
        print(f"[LOG] 开始获取矿机详细日志: {miner.ip_address}")
        
        result = {
            'pools': [],
            'boards': [],
            'raw_logs': []
        }
        
        # 1. 获取矿池详细信息
        pools_data = await self._send_command(miner.ip_address, "pools")
        if pools_data and 'POOLS' in pools_data:
            for idx, pool in enumerate(pools_data['POOLS'], 1):
                if isinstance(pool, dict):
                    result['pools'].append({
                        'index': idx,
                        'url': pool.get('URL', 'Unknown'),
                        'user': pool.get('User', 'Unknown'),
                        'status': pool.get('Status', 'Unknown'),
                        'priority': pool.get('Priority', 0),
                        'accepted': pool.get('Accepted', 0),
                        'rejected': pool.get('Rejected', 0),
                        'stale': pool.get('Stale', 0),
                        'last_share_time': pool.get('Last Share Time', 0),
                        'difficulty': pool.get('Diff', 0),
                        'proxy': pool.get('Proxy', '')
                    })
        
        # 2. 获取算力板详细信息（devs 和 stats）
        devs_data = await self._send_command(miner.ip_address, "devs")
        stats_data = await self._send_command(miner.ip_address, "stats")
        
        # 解析算力板信息
        if devs_data and 'DEVS' in devs_data:
            for idx, dev in enumerate(devs_data['DEVS'], 1):
                if isinstance(dev, dict):
                    board_info = {
                        'index': idx,
                        'name': dev.get('Name', f'Board {idx}'),
                        'id': dev.get('ID', idx - 1),
                        'enabled': dev.get('Enabled', 'N'),
                        'status': dev.get('Status', 'Unknown'),
                        'temperature': dev.get('Temperature', 0),
                        'mhs_av': dev.get('MHS av', 0),
                        'mhs_5s': dev.get('MHS 5s', 0),
                        'accepted': dev.get('Accepted', 0),
                        'rejected': dev.get('Rejected', 0),
                        'hardware_errors': dev.get('Hardware Errors', 0),
                        'utility': dev.get('Utility', 0),
                        'last_share_pool': dev.get('Last Share Pool', -1),
                        'last_share_time': dev.get('Last Share Time', 0),
                        'chips': 0,
                        'freq': 0,
                        'voltage': 0
                    }
                    result['boards'].append(board_info)
        
        # 从 stats 获取更详细的算力板信息（温度、频率、电压等）
        if stats_data and 'STATS' in stats_data:
            for stat in stats_data['STATS']:
                if isinstance(stat, dict) and 'chain_acn' in str(stat).lower():
                    # 解析各个算力板的详细信息
                    for i in range(1, 4):  # 3个算力板
                        if len(result['boards']) >= i:
                            board = result['boards'][i-1]
                            board['chips'] = stat.get(f'chain_acn{i}', 0)
                            board['freq'] = stat.get(f'chain_rate{i}', 0)
                            board['voltage'] = stat.get(f'chain_voltage{i}', 0)
                            
                            # 获取温度
                            temps = []
                            for j in range(1, 10):
                                temp_key = f'temp{i}_{j}' if f'temp{i}_{j}' in stat else f'temp2_{(i-1)*4+j}'
                                if temp_key in stat:
                                    temps.append(stat[temp_key])
                            if temps:
                                board['temperature'] = max(temps)
        
        # 3. 获取原始日志信息
        # Summary
        summary_data = await self._send_command(miner.ip_address, "summary")
        if summary_data:
            result['raw_logs'].append({
                'timestamp': datetime.now(),
                'category': 'SUMMARY',
                'content': json.dumps(summary_data, ensure_ascii=False, indent=2)
            })
        
        # Stats
        if stats_data:
            result['raw_logs'].append({
                'timestamp': datetime.now(),
                'category': 'STATS',
                'content': json.dumps(stats_data, ensure_ascii=False, indent=2)
            })
        
        # Devs
        if devs_data:
            result['raw_logs'].append({
                'timestamp': datetime.now(),
                'category': 'DEVS',
                'content': json.dumps(devs_data, ensure_ascii=False, indent=2)
            })
        
        # Pools
        if pools_data:
            result['raw_logs'].append({
                'timestamp': datetime.now(),
                'category': 'POOLS',
                'content': json.dumps(pools_data, ensure_ascii=False, indent=2)
            })
        
        # Config
        config_data = await self._send_command(miner.ip_address, "config")
        if config_data:
            result['raw_logs'].append({
                'timestamp': datetime.now(),
                'category': 'CONFIG',
                'content': json.dumps(config_data, ensure_ascii=False, indent=2)
            })
        
        # 4. 优先获取矿机后台真实系统日志（与 Web 界面一致）
        fw_hint = None
        if summary_data:
            raw = json.dumps(summary_data).upper()
            if "LUXMINER" in raw or "LUXOR" in raw:
                fw_hint = "luxor"
            elif "BOSER" in raw or "BRAIINS" in raw or "BOSMINER" in raw:
                fw_hint = "braiins"
            elif "BMMINER" in raw or "ANTA" in raw or "ANTMINER" in raw:
                fw_hint = "bmminer"
        system_log = await self._fetch_http_log(miner.ip_address, firmware_hint=fw_hint)
        if system_log:
            result['raw_logs'].insert(0, {
                'timestamp': datetime.now(),
                'category': 'SYSTEM_LOG',
                'content': system_log,
                'description': '矿机后台系统日志（与 Web 界面一致）'
            })
        
        print(f"[LOG] 获取详细日志完成: {len(result['pools'])} 个矿池, {len(result['boards'])} 个算力板, {len(result['raw_logs'])} 条原始日志")
        return result
    
    async def fetch_logs(self, miner: Miner, limit: int = 100) -> List[Dict]:
        """获取矿机日志，优先从矿机 HTTP 接口获取与后台一致的系统日志"""
        if not miner.ip_address:
            return []
        
        print(f"[LOG] 开始获取矿机日志: {miner.ip_address}")
        
        # 优先从 HTTP 获取真实系统日志（与矿机后台一致）
        system_log = await self._fetch_http_log(miner.ip_address)
        if system_log and len(system_log) > 20:
            lines = [ln.strip() for ln in system_log.split('\n') if ln.strip()]
            logs = []
            for i, line in enumerate(lines[-limit:]):  # 取最后 limit 行
                log_type = self._parse_log_type(line)
                analysis = self._analyze_log_line(line)
                logs.append({
                    'timestamp': datetime.now(),
                    'log_type': log_type,
                    'content': line,
                    'analysis': analysis
                })
            if logs:
                print(f"[LOG] 已从矿机后台获取系统日志 {len(logs)} 条（与 Web 界面一致）")
                return logs
        
        # HTTP 失败时回退到 JSON-RPC 分析
        print(f"[LOG] HTTP 系统日志不可用，使用 JSON-RPC 分析（非原厂固件如 BraiinsOS 可能无 get_kernel_log.cgi）")
        logs = []
        
        # 1. 获取 devdetails（设备详情，包含芯片状态）
        devdetails = await self._send_command(miner.ip_address, "devdetails")
        if devdetails and 'DEVDETAILS' in devdetails:
            for dev in devdetails['DEVDETAILS']:
                if isinstance(dev, dict):
                    content = json.dumps(dev, ensure_ascii=False)
                    logs.append({
                        'timestamp': datetime.now(),
                        'log_type': 'system',
                        'content': f"设备详情: {content}",
                        'analysis': None
                    })
        
        # 2. 获取 stats（统计信息，包含错误计数）
        stats = await self._send_command(miner.ip_address, "stats")
        if stats and 'STATS' in stats:
            for stat in stats['STATS']:
                if isinstance(stat, dict):
                    # 检查是否有错误
                    hw_errors = stat.get('Hardware Errors', 0)
                    if hw_errors > 0:
                        logs.append({
                            'timestamp': datetime.now(),
                            'log_type': 'error',
                            'content': f"硬件错误数: {hw_errors}",
                            'analysis': '硬件错误过多: 可能是算力板或芯片故障，建议检修'
                        })
                    
                    # 检查温度
                    temps = [stat.get(f'temp{i}', 0) for i in range(1, 20)]
                    max_temp = max(temps) if temps else 0
                    if max_temp > 80:
                        logs.append({
                            'timestamp': datetime.now(),
                            'log_type': 'warning',
                            'content': f"温度过高: {max_temp}°C",
                            'analysis': '高温警告: 温度过高可能导致算力板保护性停机'
                        })
                    
                    # 检查风扇
                    fans = [stat.get(f'fan{i}', 0) for i in range(1, 10)]
                    min_fan = min([f for f in fans if f > 0], default=0)
                    if 0 < min_fan < 2000:
                        logs.append({
                            'timestamp': datetime.now(),
                            'log_type': 'error',
                            'content': f"风扇转速异常: {min_fan} RPM",
                            'analysis': '风扇故障: 转速过低，散热不足'
                        })
        
        # 3. 获取 devs（ASIC 设备状态）
        devs = await self._send_command(miner.ip_address, "devs")
        if devs and 'DEVS' in devs:
            for dev in devs['DEVS']:
                if isinstance(dev, dict):
                    status = dev.get('Status', '')
                    enabled = dev.get('Enabled', 'N')
                    mhs_av = dev.get('MHS av', 0)
                    
                    if status != 'Alive' or enabled != 'Y':
                        logs.append({
                            'timestamp': datetime.now(),
                            'log_type': 'error',
                            'content': f"ASIC 设备状态异常: Status={status}, Enabled={enabled}",
                            'analysis': 'ASIC 设备未激活: 算力板可能未正常工作'
                        })
                    
                    if mhs_av < 1000:  # 低于 1 GH/s
                        logs.append({
                            'timestamp': datetime.now(),
                            'log_type': 'error',
                            'content': f"ASIC 设备算力过低: {mhs_av} MH/s",
                            'analysis': '无算力或算力极低: 算力板故障或未正确配置'
                        })
        
        # 4. 获取 summary（汇总信息）
        summary = await self._send_command(miner.ip_address, "summary")
        if summary and 'SUMMARY' in summary:
            sum_data = summary['SUMMARY'][0] if summary['SUMMARY'] else {}
            
            hashrate = float(sum_data.get('GHS av', 0))
            if hashrate < 10:  # 低于 10 GH/s
                logs.append({
                    'timestamp': datetime.now(),
                    'log_type': 'error',
                    'content': f"总算力过低: {hashrate} GH/s",
                    'analysis': '无算力故障: 可能原因 - 算力板故障/电源问题/矿池连接失败'
                })
            
            hw_errors = int(sum_data.get('Hardware Errors', 0))
            if hw_errors > 100:
                logs.append({
                    'timestamp': datetime.now(),
                    'log_type': 'warning',
                    'content': f"硬件错误累计: {hw_errors}",
                    'analysis': '硬件错误频繁: 建议检查算力板和电源'
                })
        
        # 5. 获取 pools（矿池状态）
        pools = await self._send_command(miner.ip_address, "pools")
        if pools and 'POOLS' in pools:
            for pool in pools['POOLS']:
                if isinstance(pool, dict):
                    status = pool.get('Status', '')
                    if status != 'Alive':
                        logs.append({
                            'timestamp': datetime.now(),
                            'log_type': 'error',
                            'content': f"矿池连接异常: {pool.get('URL', 'Unknown')}, Status={status}",
                            'analysis': '矿池连接失败: 检查网络和矿池地址配置'
                        })
        
        print(f"[LOG] 共获取 {len(logs)} 条日志")
        return logs
    
    async def save_logs_to_db(self, miner_id: int, logs: List[Dict]):
        """保存日志到数据库"""
        try:
            for log in logs:
                db_log = MinerLog(
                    miner_id=miner_id,
                    timestamp=log['timestamp'],
                    log_type=log['log_type'],
                    content=log['content'],
                    analyzed=bool(log.get('analysis')),
                    analysis_result=log.get('analysis')
                )
                self.db.add(db_log)
            
            self.db.commit()
            print(f"[LOG] 已保存 {len(logs)} 条日志到数据库")
            return True
        except Exception as e:
            self.db.rollback()
            print(f"[ERROR] 保存日志失败: {e}")
            return False
    
    async def fetch_and_save_logs(self, miner: Miner) -> Dict:
        """获取并保存矿机日志"""
        logs = await self.fetch_logs(miner)
        
        if logs:
            success = await self.save_logs_to_db(miner.id, logs)
            return {
                'success': success,
                'count': len(logs),
                'logs': logs
            }
        else:
            return {
                'success': False,
                'count': 0,
                'logs': [],
                'message': '无法获取日志'
            }
