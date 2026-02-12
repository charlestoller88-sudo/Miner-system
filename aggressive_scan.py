"""
激进扫描模式 - 使用更长的超时和更多重试来发现所有矿机
适用于网络环境较差或矿机响应较慢的情况
"""
import asyncio
from utils import ip_scanner
import config

# 临时修改扫描配置为激进模式
ORIGINAL_CONFIG = config.IP_SCAN_CONFIG.copy()

async def aggressive_scan():
    """使用激进配置进行扫描"""
    print("=" * 80)
    print("激进扫描模式 - 发现所有可能的矿机")
    print("=" * 80)
    print("\n扫描配置:")
    print("  - 超时: 10 秒（原 5 秒）")
    print("  - 重试: 4 次（原 2 次）")
    print("  - 并发: 10（原 20）")
    print("  - 矿池过滤: poolbinance")
    print("\n预计扫描时间: 5-10 分钟")
    print("请耐心等待...\n")
    
    # 临时修改配置
    config.IP_SCAN_CONFIG['timeout'] = 10
    config.IP_SCAN_CONFIG['retry_times'] = 4
    config.IP_SCAN_CONFIG['concurrency'] = 10
    
    try:
        # 执行扫描
        result = await ip_scanner.discover_miners(
            ip_range="10.102.0.0/24,10.102.1.0/24",
            pool_filter="poolbinance"
        )
        
        print("\n" + "=" * 80)
        print("激进扫描结果")
        print("=" * 80)
        print(f"\n扫描状态: {'成功' if result['success'] else '失败'}")
        print(f"总计扫描IP: {result['total_ips']} 个")
        print(f"发现矿机: {result['count']} 台")
        
        if result.get('error'):
            print(f"错误: {result['error']}")
        
        # 显示发现的矿机
        if result['discovered']:
            print("\n" + "=" * 80)
            print("发现的矿机列表")
            print("=" * 80)
            
            miners = sorted(result['discovered'], key=lambda x: x['ip'])
            for i, miner in enumerate(miners, 1):
                print(f"{i:3d}. {miner['ip']:15s} | {miner['miner_name']:20s} | {miner['pool_url']}")
            
            # 网段统计
            segment_0 = sum(1 for m in miners if m['ip'].startswith('10.102.0.'))
            segment_1 = sum(1 for m in miners if m['ip'].startswith('10.102.1.'))
            
            print("\n网段分布:")
            print(f"  10.102.0.0/24: {segment_0} 台")
            print(f"  10.102.1.0/24: {segment_1} 台")
            
            # 与预期对比
            if result['count'] < 112:
                print(f"\n⚠️  仍缺失 {112 - result['count']} 台矿机")
                print("可能原因:")
                print("  1. 这些矿机确实离线或关机")
                print("  2. 网络完全不通")
                print("  3. 使用了不同的矿池")
            else:
                print(f"\n✓ 发现了所有 {result['count']} 台矿机！")
        
        return result
        
    finally:
        # 恢复原配置
        config.IP_SCAN_CONFIG.update(ORIGINAL_CONFIG)
        print("\n配置已恢复为默认值")

if __name__ == "__main__":
    asyncio.run(aggressive_scan())
