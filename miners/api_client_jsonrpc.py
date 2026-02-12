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
            
            if 'STATUS' not in data or not data['STATUS']:
                return None
            
            status = data['STATUS'][0]
            if status.get('STATUS') != 'S':
                return None
            
            return data
            
        except Exception as e:
            print(f"[ERROR] JSON-RPC 命令失败 {self.ip}:{command} - {e}")
            return None
    
    async def get_summary(self) -> Optional[Dict]:
        """获取汇总信息"""
        print(f"[DEBUG] JSON-RPC 获取 summary: {self.ip}")
        data = await self._send_command("summary")
        if not data or 'SUMMARY' not in data:
            return None
        
        summary = data['SUMMARY'][0] if data['SUMMARY'] else {}
        
        result = {
            'ip': self.ip,
            'timestamp': datetime.now(),
            'model': 'Antminer',
            'hashrate': float(summary.get('GHS av', 0)) / 1000,  # 转 TH/s
            'power_usage': float(summary.get('Power', summary.get('Watt', 0))),
            'uptime': int(summary.get('Elapsed', 0)),
            'hw_errors': int(summary.get('Hardware Errors', 0)),
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
    
    async def get_full_summary(self) -> Optional[Dict]:
        """获取完整汇总信息（整合多个命令）"""
        print(f"[DEBUG] ===== JSON-RPC 开始获取完整信息: {self.ip} =====")
        
        summary_data = await self.get_summary()
        if not summary_data:
            print(f"[ERROR] summary 为空，返回 None")
            return None
        
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
