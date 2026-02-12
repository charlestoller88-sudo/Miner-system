"""
IP范围扫描器 - 根据IP地址范围发现网络中的矿机
支持CIDR格式、IP范围格式，可按矿池进行过滤
"""
import asyncio
import json
import ipaddress
import time
from typing import List, Dict, Optional
import config


def parse_ip_range(ip_range_str: str) -> List[str]:
    """
    解析IP范围字符串，返回IP地址列表
    支持格式:
    - CIDR: 10.102.0.0/24
    - 范围: 10.102.0.1-10.102.0.100
    - 单个IP: 10.102.0.1
    - 多个(逗号分隔): 10.102.0.0/24,10.102.1.1-10.102.1.50
    """
    ip_list = []
    parts = [p.strip() for p in ip_range_str.replace('，', ',').split(',') if p.strip()]
    
    for part in parts:
        # CIDR格式
        if '/' in part:
            try:
                network = ipaddress.ip_network(part, strict=False)
                ip_list.extend([str(ip) for ip in network.hosts()])
            except ValueError:
                continue
        # 范围格式 1.2.3.4-1.2.3.100
        elif '-' in part:
            try:
                start_str, end_str = part.split('-', 1)
                start_ip = ipaddress.ip_address(start_str.strip())
                end_ip = ipaddress.ip_address(end_str.strip())
                if start_ip > end_ip:
                    start_ip, end_ip = end_ip, start_ip
                ip = start_ip
                while ip <= end_ip:
                    ip_list.append(str(ip))
                    ip += 1
            except ValueError:
                continue
        # 单个IP
        else:
            try:
                ip = ipaddress.ip_address(part)
                if not ip.is_loopback and not ip.is_multicast:
                    ip_list.append(str(ip))
            except ValueError:
                continue
    
    return list(dict.fromkeys(ip_list))  # 去重保持顺序


async def probe_miner_jsonrpc(ip: str, port: int = 4028, timeout: float = None, retry_times: int = 0) -> Optional[Dict]:
    """
    通过Antminer JSON-RPC (端口4028) 探测矿机
    发送pools命令获取矿池信息，无需认证
    返回矿机数据或None
    """
    timeout = timeout or config.IP_SCAN_CONFIG.get('timeout', 5)
    retry_times = retry_times or config.IP_SCAN_CONFIG.get('retry_times', 2)
    retry_delay = config.IP_SCAN_CONFIG.get('retry_delay', 0.5)
    
    # 尝试多次
    for attempt in range(retry_times + 1):
        result = await _probe_miner_once(ip, port, timeout)
        if result is not None:
            return result
        
        # 如果不是最后一次尝试，等待后重试
        if attempt < retry_times:
            await asyncio.sleep(retry_delay)
    
    return None


async def _probe_miner_once(ip: str, port: int, timeout: float) -> Optional[Dict]:
    """
    单次探测矿机（内部函数）
    """
    
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(ip, port),
            timeout=timeout
        )
        
        # 发送pools命令获取矿池信息
        command = json.dumps({"command": "pools"}) + "\n"
        writer.write(command.encode())
        await writer.drain()
        
        response_data = b""
        start_time = time.time()
        try:
            while True:
                chunk = await asyncio.wait_for(reader.read(4096), timeout=2.0)
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
        
        result = {'ip': ip, 'port': port}
        
        # 解析矿池信息
        if 'POOLS' in data and data['POOLS']:
            for pool in data['POOLS']:
                if pool.get('Status') == 'Alive':
                    result['pool_url'] = pool.get('URL', '')
                    result['pool_user'] = pool.get('User', '')
                    # 从矿工账号提取矿机名称（如 NCATX.082）
                    user = pool.get('User', '')
                    if user and '.' in user:
                        # 矿工账号格式通常为: 账户名.矿机名 (如 account.NCATX.082 或 NCATX.082)
                        parts = user.split('.')
                        if len(parts) >= 2:
                            result['miner_name'] = '.'.join(parts[-2:])  # 取最后两部分
                        else:
                            result['miner_name'] = user
                    break
            if 'pool_url' not in result and data['POOLS']:
                p = data['POOLS'][0]
                result['pool_url'] = p.get('URL', '')
                result['pool_user'] = p.get('User', '')
                user = p.get('User', '')
                if user and '.' in user:
                    parts = user.split('.')
                    if len(parts) >= 2:
                        result['miner_name'] = '.'.join(parts[-2:])
                    else:
                        result['miner_name'] = user
        
        # 尝试获取矿机型号
        model = 'Antminer'  # 默认值
        firmware = ''
        
        try:
            # 方法1: 从 version 命令获取型号（最可靠）
            try:
                cmd_version = json.dumps({"command": "version"}) + "\n"
                rv, wv = await asyncio.wait_for(asyncio.open_connection(ip, port), timeout=timeout)
                wv.write(cmd_version.encode())
                await wv.drain()
                data_v = b""
                try:
                    while True:
                        c = await asyncio.wait_for(rv.read(4096), timeout=2.0)
                        if not c:
                            break
                        data_v += c
                        if b'}' in data_v and data_v.decode('utf-8', errors='ignore').strip().endswith('}'):
                            break
                except asyncio.TimeoutError:
                    pass
                wv.close()
                try:
                    await wv.wait_closed()
                except Exception:
                    pass
                
                if data_v:
                    tv = data_v.decode('utf-8', errors='ignore')
                    jvs, jve = tv.find('{'), tv.rfind('}') + 1
                    if jvs >= 0 and jve > jvs:
                        dv = json.loads(tv[jvs:jve])
                        if 'VERSION' in dv and dv['VERSION']:
                            version_info = dv['VERSION'][0]
                            # 检查 Type 字段（原厂固件会有）
                            if 'Type' in version_info and version_info['Type'].strip():
                                model = version_info['Type'].strip()
                            # 检查 Miner 字段
                            elif 'Miner' in version_info and version_info['Miner'].strip():
                                miner_val = version_info['Miner'].strip()
                                if 'S19' in miner_val or 'S17' in miner_val or 'T19' in miner_val:
                                    model = miner_val
                            # 保存固件信息
                            if 'BMMiner' in version_info:
                                firmware = version_info['BMMiner']
                            elif 'BOSer' in version_info:
                                firmware = f"BOSer {version_info['BOSer']}"
            except Exception:
                pass
            
            # 方法2: 从 stats 命令获取（如果 version 没有获取到）
            if model == 'Antminer':
                try:
                    command2 = json.dumps({"command": "stats"}) + "\n"
                    r2, w2 = await asyncio.wait_for(asyncio.open_connection(ip, port), timeout=timeout)
                    w2.write(command2.encode())
                    await w2.drain()
                    data2 = b""
                    try:
                        while True:
                            c = await asyncio.wait_for(r2.read(4096), timeout=2.0)
                            if not c:
                                break
                            data2 += c
                            if b'}' in data2 and data2.decode('utf-8', errors='ignore').strip().endswith('}'):
                                break
                    except asyncio.TimeoutError:
                        pass
                    w2.close()
                    try:
                        await w2.wait_closed()
                    except Exception:
                        pass
                    
                    if data2:
                        t2 = data2.decode('utf-8', errors='ignore')
                        j2s, j2e = t2.find('{'), t2.rfind('}') + 1
                        if j2s >= 0 and j2e > j2s:
                            d2 = json.loads(t2[j2s:j2e])
                            if 'STATS' in d2 and d2['STATS']:
                                # 遍历所有 STATS 条目寻找型号信息
                                for stat_item in d2['STATS']:
                                    # 尝试多个可能的字段名（不区分大小写）
                                    for key, value in stat_item.items():
                                        key_lower = key.lower()
                                        if key_lower in ['type', 'miner', 'miner_type', 'minertype', 
                                                       'model', 'hardware_version', 'hw_version']:
                                            if isinstance(value, str) and value.strip() and value.strip() != 'Antminer':
                                                model = value.strip()
                                                break
                                    
                                    # 保存 BMMiner 版本
                                    if 'BMMiner' in stat_item:
                                        firmware = stat_item['BMMiner']
                                    
                                    if model != 'Antminer':
                                        break
                                
                                # 方法2: 如果还是默认值，尝试从 BMMiner 版本推断
                                if model == 'Antminer' and firmware:
                                    firmware_upper = firmware.upper()
                                    if 'S19' in firmware_upper:
                                        if 'XP' in firmware_upper:
                                            model = 'Antminer S19 XP'
                                        elif 'PRO' in firmware_upper or 'J PRO' in firmware_upper:
                                            model = 'Antminer S19 Pro'
                                        elif 'J' in firmware_upper:
                                            model = 'Antminer S19j'
                                        else:
                                            model = 'Antminer S19'
                                    elif 'S17' in firmware_upper:
                                        if 'PRO' in firmware_upper:
                                            model = 'Antminer S17 Pro'
                                        elif '+' in firmware_upper:
                                            model = 'Antminer S17+'
                                        else:
                                            model = 'Antminer S17'
                                    elif 'T19' in firmware_upper:
                                        model = 'Antminer T19'
                                    elif 'T17' in firmware_upper:
                                        if '+' in firmware_upper:
                                            model = 'Antminer T17+'
                                        else:
                                            model = 'Antminer T17'
                                
                                # 方法3: 如果仍然是默认值，尝试从其他 STATS 字段推断
                                if model == 'Antminer' and d2['STATS']:
                                    first_stat = d2['STATS'][0] if len(d2['STATS']) > 0 else {}
                                    # 检查是否有 chain 相关的字段（通常高端机型会有）
                                    has_chains = any('chain' in str(k).lower() for k in first_stat.keys())
                                    # 检查风扇数量（S19 XP 通常有4个风扇）
                                    fan_count = sum(1 for k in first_stat.keys() if 'fan' in str(k).lower() and isinstance(first_stat.get(k), (int, float)))
                                    
                                    # 如果有多个算力板和多个风扇，很可能是 S19 系列
                                    if has_chains and fan_count >= 4:
                                        # 假设是 S19 XP（您的矿场主要型号）
                                        model = 'Antminer S19 XP'
                                
                                result['firmware'] = firmware
                except Exception:
                    pass
        except Exception as e:
            pass
        
        # 方法4: 如果仍然是默认值，检查固件信息
        # BOSer 固件的矿机通常是 S19 系列（根据您的矿场情况）
        if model == 'Antminer' and firmware and 'BOSer' in firmware:
            # BOSer 是第三方固件，通常用于 S19 XP
            # 根据您的矿场实际情况，假设是 S19 XP
            model = 'Antminer S19 XP'
        
        result['model'] = model
        
        return result
        
    except (asyncio.TimeoutError, ConnectionRefusedError, OSError):
        return None
    except Exception:
        return None


def match_pool_filter(pool_url: str, pool_filter: Optional[str]) -> bool:
    """
    检查矿池URL是否匹配过滤条件
    支持模糊匹配，例如：
    - 过滤条件: "binance" 可以匹配 "stratum+tcp://sha256.poolbinance.com:443"
    - 过滤条件: "poolbinance" 可以匹配 "stratum+tcp://sha256.poolbinance.com:443"
    """
    if not pool_filter or not pool_filter.strip():
        return True
    
    pool_url_lower = (pool_url or '').lower()
    filter_key = pool_filter.strip().lower()
    
    # 支持多个关键词，用逗号或空格分隔
    keywords = [k.strip() for k in filter_key.replace(',', ' ').split() if k.strip()]
    
    if not keywords:
        return True
    
    # 只要匹配任意一个关键词即可
    for keyword in keywords:
        if keyword in pool_url_lower:
            return True
    
    return False


async def scan_ip_list(
    ip_list: List[str],
    port: int = 4028,
    pool_filter: Optional[str] = None,
    concurrency: int = 20
) -> List[Dict]:
    """
    扫描IP列表，发现矿机并按矿池过滤
    """
    port = port or config.IP_SCAN_CONFIG.get('port', 4028)
    concurrency = concurrency or config.IP_SCAN_CONFIG.get('concurrency', 20)
    
    print(f"[IP扫描] 开始扫描 {len(ip_list)} 个IP地址，并发数: {concurrency}")
    print(f"[IP扫描] 矿池过滤: {pool_filter or '无过滤'}")
    
    semaphore = asyncio.Semaphore(concurrency)
    discovered = []
    scanned_count = 0
    responsive_count = 0
    filtered_count = 0
    
    async def check_one(ip: str) -> Optional[Dict]:
        nonlocal scanned_count, responsive_count, filtered_count
        async with semaphore:
            data = await probe_miner_jsonrpc(ip, port)
            scanned_count += 1
            
            if scanned_count % 50 == 0:
                print(f"[IP扫描] 进度: {scanned_count}/{len(ip_list)}, 已发现: {len(discovered)} 台矿机")
            
            if not data:
                return None
            
            responsive_count += 1
            
            if not match_pool_filter(data.get('pool_url', ''), pool_filter):
                filtered_count += 1
                print(f"[IP扫描] {ip} 矿池不匹配: {data.get('pool_url', 'N/A')}")
                return None
            
            print(f"[IP扫描] ✓ 发现矿机: {ip} - {data.get('miner_name', 'Unknown')} - {data.get('pool_url', 'N/A')}")
            return data
    
    tasks = [check_one(ip) for ip in ip_list]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    for r in results:
        if isinstance(r, dict):
            discovered.append(r)
        elif isinstance(r, Exception):
            print(f"[IP扫描] 扫描异常: {r}")
    
    print(f"[IP扫描] 扫描完成！")
    print(f"[IP扫描] 总计扫描: {scanned_count} 个IP")
    print(f"[IP扫描] 响应矿机: {responsive_count} 台")
    print(f"[IP扫描] 过滤排除: {filtered_count} 台")
    print(f"[IP扫描] 最终发现: {len(discovered)} 台")
    
    return discovered


async def discover_miners(
    ip_range: str,
    pool_filter: Optional[str] = None,
    port: int = None
) -> Dict:
    """
    根据IP范围和矿池过滤条件发现矿机
    返回: {
        'success': bool,
        'discovered': [...],
        'total_ips': int,
        'count': int,
        'error': str (可选)
    }
    """
    try:
        ip_list = parse_ip_range(ip_range)
        if not ip_list:
            return {
                'success': False,
                'discovered': [],
                'total_ips': 0,
                'count': 0,
                'error': '无效的IP范围'
            }
        
        discovered = await scan_ip_list(ip_list, port=port, pool_filter=pool_filter)
        
        return {
            'success': True,
            'discovered': discovered,
            'total_ips': len(ip_list),
            'count': len(discovered)
        }
    except Exception as e:
        return {
            'success': False,
            'discovered': [],
            'total_ips': 0,
            'count': 0,
            'error': str(e)
        }
