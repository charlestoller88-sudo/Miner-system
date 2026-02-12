"""
诊断矿机 API 返回数据
查看未识别型号的矿机实际返回了什么数据
"""
import asyncio
import json
from miners.api_client_jsonrpc import AntminerAPIJsonRPC

# 选择几个未识别型号的矿机 IP 进行诊断
SAMPLE_IPS = [
    "10.102.0.208",  # NCATX.055 - 未识别
    "10.102.0.215",  # NCATX.096 - 未识别
    "10.102.1.1",    # NCATX.086 - 未识别
    "10.102.0.210",  # NCATX.082 - 已识别为 S19 XP（用于对比）
]

async def diagnose_miner(ip):
    """诊断单个矿机的 API 返回数据"""
    print("\n" + "=" * 80)
    print(f"诊断矿机: {ip}")
    print("=" * 80)
    
    api = AntminerAPIJsonRPC(ip, port=4028)
    
    # 1. 获取 stats 命令
    print("\n【1】 stats 命令返回:")
    print("-" * 80)
    try:
        stats_result = await api.get_stats()
        if stats_result:
            # 打印完整的 STATS 数据
            print(json.dumps(stats_result, indent=2, ensure_ascii=False))
            
            # 检查可能的型号字段
            print("\n检查可能的型号字段:")
            if isinstance(stats_result, list):
                for i, item in enumerate(stats_result):
                    print(f"\n  STATS[{i}]:")
                    for key in ['Type', 'Miner', 'miner_type', 'type', 'MinerType', 
                               'hardware_version', 'BMMiner', 'Model', 'model']:
                        if key in item:
                            print(f"    {key}: {item[key]}")
        else:
            print("未返回 stats 数据")
    except Exception as e:
        print(f"获取 stats 失败: {e}")
    
    # 2. 获取 version 命令
    print("\n【2】 version 命令返回:")
    print("-" * 80)
    try:
        version_result = await api._send_command("version")
        if version_result:
            print(json.dumps(version_result, indent=2, ensure_ascii=False))
    except Exception as e:
        print(f"获取 version 失败: {e}")
    
    # 3. 获取 devs 命令
    print("\n【3】 devs 命令返回（查找型号相关字段）:")
    print("-" * 80)
    try:
        devs_result = await api._send_command("devs")
        if devs_result and 'DEVS' in devs_result:
            # 只打印第一个设备的关键字段
            if devs_result['DEVS']:
                dev = devs_result['DEVS'][0]
                print("第一个设备的关键字段:")
                for key in dev.keys():
                    if any(keyword in key.lower() for keyword in ['model', 'type', 'name', 'version']):
                        print(f"  {key}: {dev[key]}")
    except Exception as e:
        print(f"获取 devs 失败: {e}")

async def main():
    print("=" * 80)
    print("矿机 API 诊断工具")
    print("=" * 80)
    print("\n这个工具会查看几台矿机的 API 返回数据")
    print("帮助我们找到正确的型号字段名称\n")
    
    for ip in SAMPLE_IPS:
        await diagnose_miner(ip)
        await asyncio.sleep(1)  # 避免请求太快
    
    print("\n" + "=" * 80)
    print("诊断完成")
    print("=" * 80)
    print("\n请将以上输出发给我，我会根据实际返回的数据优化型号识别逻辑")

if __name__ == "__main__":
    asyncio.run(main())
