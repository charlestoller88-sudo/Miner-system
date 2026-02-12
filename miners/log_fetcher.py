"""
矿机日志获取器 - 从 Antminer JSON-RPC 获取系统日志
"""
import asyncio
import json
import time
import re
from datetime import datetime
from typing import List, Dict, Optional
from sqlalchemy.orm import Session
from database.models import Miner, MinerLog


class MinerLogFetcher:
    """矿机日志获取器"""
    
    def __init__(self, db: Session):
        self.db = db
    
    async def _send_command(self, ip: str, command: str, port: int = 4028, timeout: float = 10) -> Optional[Dict]:
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
            
            if 'STATUS' not in data or not data['STATUS']:
                return None
            
            status = data['STATUS'][0]
            if status.get('STATUS') != 'S':
                return None
            
            return data
            
        except Exception as e:
            print(f"[ERROR] 日志获取命令失败 {ip}:{command} - {e}")
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
        
        print(f"[LOG] 获取详细日志完成: {len(result['pools'])} 个矿池, {len(result['boards'])} 个算力板, {len(result['raw_logs'])} 条原始日志")
        return result
    
    async def fetch_logs(self, miner: Miner, limit: int = 100) -> List[Dict]:
        """获取矿机日志"""
        if not miner.ip_address:
            return []
        
        print(f"[LOG] 开始获取矿机日志: {miner.ip_address}")
        
        # 尝试获取各种日志信息
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
