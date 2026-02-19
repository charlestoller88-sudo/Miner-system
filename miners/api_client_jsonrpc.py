"""
基于 JSON-RPC (端口 4028) 的 Antminer API 客户端
与 ip_scanner 使用相同协议，避免 HTTP CGI 兼容性问题
"""
import asyncio
import json
import time
from datetime import datetime
from typing import Optional, Dict


class AntminerAPIJsonRPC:
    """使用 JSON-RPC 的 Antminer API 客户端（端口 4028，无需认证）"""
    
    def __init__(self, ip: str, port: int = 4028, timeout: float = 10):
        self.ip = ip
        self.port = port
        self.timeout = timeout
    
    async def _send_command(self, command: str) -> Optional[Dict]:
        """发送 JSON-RPC 命令"""
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self.ip, self.port),
                timeout=self.timeout
            )
            
            cmd = json.dumps({"command": command}) + "\n"
            writer.write(cmd.encode())
            await writer.drain()
            
            response_data = b""
            start_time = time.time()
            
            try:
                while True:
                    chunk = await asyncio.wait_for(reader.read(4096), timeout=3.0)
                    if not chunk:
                        break
                    response_data += chunk
                    if time.time() - start_time > self.timeout:
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
            
            # 部分固件可能返回不同格式，放宽 STATUS 校验
            if 'STATUS' in data and data['STATUS']:
                status = data['STATUS'][0]
                code = str(status.get('STATUS', '')).upper()
                # 'S'=成功, 'E'=错误；无 STATUS 时若有 SUMMARY 也可尝试解析
                if code == 'E':
                    return None
            elif 'SUMMARY' not in data:
                return None
            
            return data
            
        except Exception as e:
            print(f"[ERROR] JSON-RPC 命令失败 {self.ip}:{command} - {e}")
            return None
    
    def _parse_hashrate_from_summary(self, summary: dict) -> float:
        """
        从 SUMMARY 解析算力，兼容多种固件格式
        不同固件可能使用: GHS av, GHS 5s, GHS 15m, MHS av, MHS 5s, Hashrate 等
        """
        # 尝试多种字段名（优先级：GHS av > GHS 5s > GHS 15m > MHS av > Hashrate）
        candidates = [
            ("GHS av", 1000),   # GH/s -> TH/s 除以 1000
            ("GHS 5s", 1000),
            ("GHS 15m", 1000),
            ("GH Safrompool", 1000),
            ("MHS av", 1000000),  # MH/s -> TH/s 除以 1000000
            ("MHS 5s", 1000000),
            ("Hashrate", 1000),
            ("hashrate", 1000),
        ]
        for field, divisor in candidates:
            val = summary.get(field)
            if val is not None:
                try:
                    v = float(val)
                    if v > 0:
                        return v / divisor
                except (TypeError, ValueError):
                    pass
        return 0.0

    async def get_summary(self) -> Optional[Dict]:
        """获取汇总信息"""
        print(f"[DEBUG] JSON-RPC 获取 summary: {self.ip}")
        data = await self._send_command("summary")
        if not data or 'SUMMARY' not in data:
            return None
        
        summary = data['SUMMARY'][0] if data['SUMMARY'] else {}
        
        hashrate = self._parse_hashrate_from_summary(summary)
        power = float(summary.get('Power', summary.get('Watt', summary.get('power', 0))))
        
        result = {
            'ip': self.ip,
            'timestamp': datetime.now(),
            'model': 'Antminer',
            'hashrate': hashrate,
            'power_usage': power,
            'uptime': int(summary.get('Elapsed', summary.get('elapsed', 0))),
            'hw_errors': int(summary.get('Hardware Errors', summary.get('hw_errors', 0))),
        }
        
        print(f"[DEBUG] summary 解析: hashrate={result['hashrate']}, power={result['power_usage']}")
        return result
    
    async def get_stats(self) -> Optional[Dict]:
        """获取统计信息（温度、风扇）"""
        print(f"[DEBUG] JSON-RPC 获取 stats: {self.ip}")
        data = await self._send_command("stats")
        if not data or 'STATS' not in data:
            return None
        
        # 通常 STATS[0] 是 cgminer 版本，STATS[1] 才是矿机数据
        stats_data = None
        for item in data['STATS']:
            if isinstance(item, dict) and 'temp' in str(item).lower():
                stats_data = item
                break
        
        if not stats_data:
            stats_data = data['STATS'][-1] if data['STATS'] else {}
        
        result = {
            'temperature': max([
                int(stats_data.get(f'temp{i}', 0)) 
                for i in range(1, 20)
            ] + [0]),
            'fan_speed': int(stats_data.get('fan1', stats_data.get('fan_1', 0))),
        }
        
        print(f"[DEBUG] stats 解析: temp={result['temperature']}, fan={result['fan_speed']}")
        return result
    
    async def get_pools(self) -> Optional[Dict]:
        """获取矿池信息"""
        print(f"[DEBUG] JSON-RPC 获取 pools: {self.ip}")
        data = await self._send_command("pools")
        if not data or 'POOLS' not in data:
            return None
        
        pools = data['POOLS']
        if not pools:
            return None
        
        # 找第一个活动的矿池
        active_pool = None
        for pool in pools:
            if pool.get('Status') == 'Alive':
                active_pool = pool
                break
        
        if not active_pool and pools:
            active_pool = pools[0]
        
        if not active_pool:
            return None
        
        result = {
            'pool': active_pool.get('URL', ''),
            'pool_user': active_pool.get('User', ''),
        }
        
        print(f"[DEBUG] pools 解析: {result['pool']}")
        return result
    
    async def _get_hashrate_from_devs(self) -> Optional[float]:
        """从 DEVS 命令汇总各算力板算力（当 summary 无有效算力时作为备用）"""
        data = await self._send_command("devs")
        if not data or 'DEVS' not in data:
            return None
        total_ghs = 0
        for dev in data.get('DEVS', []):
            if isinstance(dev, dict):
                mhs = float(dev.get('MHS av', dev.get('mhs_av', 0)) or 0)
                ghs = float(dev.get('GHS av', dev.get('ghs_av', 0)) or 0)
                if mhs > 0:
                    total_ghs += mhs / 1000  # MH/s -> GH/s
                elif ghs > 0:
                    total_ghs += ghs
        return total_ghs / 1000 if total_ghs > 0 else None  # GH/s -> TH/s

    async def get_full_summary(self) -> Optional[Dict]:
        """获取完整汇总信息（整合多个命令）"""
        print(f"[DEBUG] ===== JSON-RPC 开始获取完整信息: {self.ip} =====")
        
        summary_data = await self.get_summary()
        if not summary_data:
            print(f"[ERROR] summary 为空，返回 None")
            return None
        
        # 若 summary 算力为 0 但矿机可能在线，尝试从 DEVS 获取（部分固件 summary 格式不同）
        if (summary_data.get('hashrate') or 0) <= 0:
            devs_hashrate = await self._get_hashrate_from_devs()
            if devs_hashrate and devs_hashrate > 0:
                summary_data['hashrate'] = devs_hashrate
                print(f"[DEBUG] 从 DEVS 获取算力: {devs_hashrate} TH/s")
        
        stats_data = await self.get_stats()
        pools_data = await self.get_pools()
        
        # 合并数据
        result = {**summary_data}
        
        if stats_data:
            result['temperature'] = stats_data['temperature']
            result['fan_speed'] = stats_data['fan_speed']
        else:
            result['temperature'] = 0
            result['fan_speed'] = 0
        
        if pools_data:
            result['pool'] = pools_data['pool']
            result['pool_user'] = pools_data.get('pool_user', '')
        else:
            result['pool'] = ''
        
        print(f"[DEBUG] 完整数据: hashrate={result.get('hashrate')}, temp={result.get('temperature')}, pool={result.get('pool')}")
        return result
