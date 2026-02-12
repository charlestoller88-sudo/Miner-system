"""
分析缺失的矿机 IP 地址
对比已发现的 92 台和预期的 112 台，找出哪些 IP 可能有矿机但未被发现
"""
import asyncio
from utils.ip_scanner import probe_miner_jsonrpc, parse_ip_range

# 已发现的 92 台矿机 IP（从测试结果中提取）
DISCOVERED_IPS = {
    "10.102.0.208", "10.102.0.210", "10.102.0.211", "10.102.0.213", "10.102.0.214",
    "10.102.0.215", "10.102.0.216", "10.102.0.217", "10.102.0.218", "10.102.0.219",
    "10.102.0.220", "10.102.0.221", "10.102.0.222", "10.102.0.223", "10.102.0.224",
    "10.102.0.227", "10.102.0.228", "10.102.0.229", "10.102.0.230", "10.102.0.232",
    "10.102.0.233", "10.102.0.234", "10.102.0.235", "10.102.0.236", "10.102.0.237",
    "10.102.0.238", "10.102.0.239", "10.102.0.240", "10.102.0.241", "10.102.0.242",
    "10.102.0.243", "10.102.0.244", "10.102.0.245", "10.102.0.246", "10.102.0.247",
    "10.102.0.249", "10.102.0.250", "10.102.0.253", "10.102.0.254",
    "10.102.1.1", "10.102.1.2", "10.102.1.4", "10.102.1.6", "10.102.1.7",
    "10.102.1.8", "10.102.1.9", "10.102.1.11", "10.102.1.13", "10.102.1.14",
    "10.102.1.15", "10.102.1.16", "10.102.1.17", "10.102.1.18", "10.102.1.19",
    "10.102.1.20", "10.102.1.21", "10.102.1.22", "10.102.1.23", "10.102.1.24",
    "10.102.1.25", "10.102.1.26", "10.102.1.29", "10.102.1.31", "10.102.1.32",
    "10.102.1.33", "10.102.1.34", "10.102.1.35", "10.102.1.36", "10.102.1.37",
    "10.102.1.38", "10.102.1.40", "10.102.1.41", "10.102.1.43", "10.102.1.44",
    "10.102.1.46", "10.102.1.47", "10.102.1.48", "10.102.1.49", "10.102.1.50",
    "10.102.1.52", "10.102.1.53", "10.102.1.54", "10.102.1.55", "10.102.1.57",
    "10.102.1.58", "10.102.1.59", "10.102.1.60", "10.102.1.61", "10.102.1.62",
    "10.102.1.63", "10.102.1.64", "10.102.1.65",
}

async def check_specific_ips(ip_list):
    """检查特定 IP 列表，看是否有矿机响应"""
    print(f"\n检查 {len(ip_list)} 个可疑 IP 地址...\n")
    
    results = {
        'responsive': [],      # 有响应但矿池不匹配
        'no_response': [],     # 无响应
        'discovered': []       # 发现新矿机
    }
    
    for ip in ip_list:
        print(f"测试 {ip}...", end=" ")
        
        # 不使用矿池过滤，看是否有任何响应
        data = await probe_miner_jsonrpc(ip, port=4028, timeout=10, retry_times=3)
        
        if data:
            pool_url = data.get('pool_url', 'N/A')
            miner_name = data.get('miner_name', 'Unknown')
            
            if 'poolbinance' in pool_url.lower():
                print(f"✓ 发现矿机 (poolbinance): {miner_name} - {pool_url}")
                results['discovered'].append({
                    'ip': ip,
                    'name': miner_name,
                    'pool': pool_url
                })
            else:
                print(f"⚠ 有响应但矿池不匹配: {pool_url}")
                results['responsive'].append({
                    'ip': ip,
                    'name': miner_name,
                    'pool': pool_url
                })
        else:
            print("✗ 无响应")
            results['no_response'].append(ip)
    
    return results

async def main():
    print("=" * 80)
    print("缺失矿机分析工具")
    print("=" * 80)
    print(f"\n已发现矿机: {len(DISCOVERED_IPS)} 台")
    print(f"预期矿机: 112 台")
    print(f"缺失: {112 - len(DISCOVERED_IPS)} 台\n")
    
    # 方法1: 检查常用 IP 范围（通常矿机会分配连续 IP）
    print("=" * 80)
    print("方法 1: 检查已发现矿机周围的连续 IP")
    print("=" * 80)
    
    # 分析已发现的 IP 范围
    segment_0_ips = sorted([ip for ip in DISCOVERED_IPS if ip.startswith("10.102.0.")])
    segment_1_ips = sorted([ip for ip in DISCOVERED_IPS if ip.startswith("10.102.1.")])
    
    if segment_0_ips:
        start_0 = int(segment_0_ips[0].split('.')[-1])
        end_0 = int(segment_0_ips[-1].split('.')[-1])
        print(f"\n10.102.0.0/24 已发现范围: .{start_0} 至 .{end_0}")
    
    if segment_1_ips:
        start_1 = int(segment_1_ips[0].split('.')[-1])
        end_1 = int(segment_1_ips[-1].split('.')[-1])
        print(f"10.102.1.0/24 已发现范围: .{start_1} 至 .{end_1}")
    
    # 生成可疑 IP 列表（在已发现范围内但未被发现的 IP）
    suspicious_ips = []
    
    # 10.102.0.0/24 网段中的缺失 IP
    if segment_0_ips:
        for i in range(start_0, end_0 + 1):
            ip = f"10.102.0.{i}"
            if ip not in DISCOVERED_IPS:
                suspicious_ips.append(ip)
    
    # 10.102.1.0/24 网段中的缺失 IP
    if segment_1_ips:
        for i in range(start_1, end_1 + 1):
            ip = f"10.102.1.{i}"
            if ip not in DISCOVERED_IPS:
                suspicious_ips.append(ip)
    
    print(f"\n在已发现范围内找到 {len(suspicious_ips)} 个可疑的缺失 IP")
    
    if suspicious_ips:
        print("\n" + "=" * 80)
        print("开始深度检查可疑 IP（增加超时和重试次数）")
        print("=" * 80)
        
        results = await check_specific_ips(suspicious_ips)
        
        # 汇总结果
        print("\n" + "=" * 80)
        print("检查结果汇总")
        print("=" * 80)
        
        if results['discovered']:
            print(f"\n✓ 发现 {len(results['discovered'])} 台新矿机（使用 poolbinance）:")
            for item in results['discovered']:
                print(f"  - {item['ip']:15s} {item['name']:20s} {item['pool']}")
        
        if results['responsive']:
            print(f"\n⚠ 发现 {len(results['responsive'])} 台矿机使用其他矿池:")
            for item in results['responsive']:
                print(f"  - {item['ip']:15s} {item['name']:20s} {item['pool']}")
        
        if results['no_response']:
            print(f"\n✗ {len(results['no_response'])} 个 IP 无响应（可能离线或不是矿机）:")
            for ip in results['no_response']:
                print(f"  - {ip}")
    
    # 方法2: 询问用户是否有已知的矿机 IP 列表
    print("\n" + "=" * 80)
    print("建议")
    print("=" * 80)
    print("\n如果您有完整的矿机 IP 地址列表（例如从路由器 DHCP 或之前的 Excel）：")
    print("1. 请提供这些 IP 地址")
    print("2. 我可以帮您编写脚本逐一检查这些 IP")
    print("3. 确定哪些是真的离线，哪些只是扫描时网络波动")
    
    print("\n当前扫描配置:")
    print("  - 超时: 5 秒")
    print("  - 重试: 2 次")
    print("  - 并发: 20")
    print("\n如果缺失的矿机数量不多且都在线，建议:")
    print("  1. 增加超时到 10 秒")
    print("  2. 增加重试到 3 次")
    print("  3. 降低并发到 10")
    print("  4. 再次运行 test_full_scan.py")

if __name__ == "__main__":
    asyncio.run(main())
