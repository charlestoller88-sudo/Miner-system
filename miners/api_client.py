import aiohttp
import asyncio
from datetime import datetime
import config

class AntminerAPI:
    """Antminer API客户端"""
    
    def __init__(self, ip: str):
        self.base_url = f"http://{ip}"
        self.auth = aiohttp.BasicAuth(
            config.MINER_CREDENTIALS['username'],
            config.MINER_CREDENTIALS['password']
        )
        # 单个请求超时5秒，总超时15秒（get_summary调用多个接口）
        self.timeout = aiohttp.ClientTimeout(total=config.SCAN_CONFIG['timeout'], sock_connect=5, sock_read=5)
    
    async def get_system_info(self):
        """获取系统信息"""
        try:
            print(f"[DEBUG] 开始获取系统信息: {self.base_url}")
            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                async with session.get(
                    f"{self.base_url}/cgi-bin/get_system_info.cgi",
                    auth=self.auth
                ) as response:
                    print(f"[DEBUG] get_system_info 响应状态: {response.status}")
                    if response.status == 200:
                        data = await response.json()
                        print(f"[DEBUG] get_system_info 返回数据: {data}")
                        return data
                    else:
                        print(f"[ERROR] get_system_info 非200状态: {response.status}")
        except Exception as e:
            print(f"[ERROR] 获取系统信息失败 {self.base_url}: {str(e)}")
            import traceback
            traceback.print_exc()
        return None
    
    async def get_miner_status(self):
        """获取矿机状态"""
        try:
            print(f"[DEBUG] 开始获取矿机状态: {self.base_url}")
            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                async with session.get(
                    f"{self.base_url}/cgi-bin/get_miner_status.cgi",
                    auth=self.auth
                ) as response:
                    print(f"[DEBUG] get_miner_status 响应状态: {response.status}")
                    if response.status == 200:
                        data = await response.json()
                        print(f"[DEBUG] get_miner_status 返回数据前100字符: {str(data)[:100]}")
                        return data
                    else:
                        print(f"[ERROR] get_miner_status 非200状态: {response.status}")
        except Exception as e:
            print(f"[ERROR] 获取矿机状态失败 {self.base_url}: {str(e)}")
        return None
    
    async def get_stats(self):
        """获取统计信息"""
        try:
            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                async with session.get(
                    f"{self.base_url}/cgi-bin/stats.cgi",
                    auth=self.auth
                ) as response:
                    if response.status == 200:
                        return await response.json()
        except Exception:
            pass
        return None
    
    async def get_pools(self):
        """获取矿池信息"""
        try:
            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                async with session.get(
                    f"{self.base_url}/cgi-bin/get_pools.cgi",
                    auth=self.auth
                ) as response:
                    if response.status == 200:
                        return await response.json()
        except Exception:
            pass
        return None
    
    async def get_summary(self):
        """获取汇总信息（整合多个API）"""
        print(f"[DEBUG] ===== 开始获取汇总信息: {self.base_url} =====")
        system_info = await self.get_system_info()
        miner_status = await self.get_miner_status()
        stats = await self.get_stats()
        pools = await self.get_pools()
        
        if not system_info:
            print(f"[ERROR] system_info 为空，返回 None")
            return None
        
        print(f"[DEBUG] system_info 存在，开始解析")
        
        # 解析系统信息
        result = {
            'ip': self.base_url.replace('http://', ''),
            'timestamp': datetime.now(),
            'model': system_info.get('minertype', system_info.get('model', 'Unknown')),
            'serial_number': system_info.get('serinum', system_info.get('serial', 'Unknown')),
            'firmware': system_info.get('firmware_type', system_info.get('firmware', 'Unknown')),
        }
        
        # 解析矿机状态
        if miner_status:
            # 根据不同型号的API结构进行解析
            if 'chain' in miner_status:
                # S19 XP等新型号
                chains = miner_status.get('chain', [])
                total_hashrate = 0
                total_power = 0
                temperatures = []
                fan_speeds = []
                
                for chain in chains:
                    if isinstance(chain, dict):
                        total_hashrate += float(chain.get('hashrate', 0))
                        total_power += float(chain.get('power', 0))
                        temp = chain.get('temp', [])
                        if isinstance(temp, list) and len(temp) > 0:
                            temperatures.extend([t for t in temp if t > 0])
                        fan = chain.get('fan', [])
                        if isinstance(fan, list):
                            fan_speeds.extend([f for f in fan if f > 0])
                
                result['hashrate'] = total_hashrate / 1000  # 转换为TH/s
                result['power_usage'] = total_power
                result['temperature'] = max(temperatures) if temperatures else 0
                result['fan_speed'] = sum(fan_speeds) / len(fan_speeds) if fan_speeds else 0
                result['hw_errors'] = sum(chain.get('hw_errors', 0) for chain in chains if isinstance(chain, dict))
            
            elif 'SUMMARY' in miner_status:
                # 其他型号
                summary = miner_status.get('SUMMARY', [{}])[0]
                result['hashrate'] = float(summary.get('GHS 5s', 0)) / 1000
                result['power_usage'] = float(summary.get('Watt', 0))
        
        # 解析矿池信息
        if pools:
            pool_list = pools.get('pools', [])
            if pool_list and len(pool_list) > 0:
                result['pool'] = pool_list[0].get('url', 'Unknown')
                result['pool_user'] = pool_list[0].get('user', 'Unknown')
        
        return result