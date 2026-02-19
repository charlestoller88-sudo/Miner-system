"""
矿机 API 诊断脚本 - 用于排查扫描结果不准确问题
可检测不同固件返回的字段格式差异
用法: python scripts/diagnose_miner_api.py 10.102.1.64 10.102.1.58 10.102.1.59
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from miners.api_client_jsonrpc import AntminerAPIJsonRPC


async def raw_summary(ip: str, port: int = 4028) -> dict:
    """获取 summary 命令的原始响应"""
    try:
        api = AntminerAPIJsonRPC(ip, port=port, timeout=10)
        data = await api._send_command("summary")
        return {"ip": ip, "success": data is not None, "raw": data}
    except Exception as e:
        return {"ip": ip, "success": False, "error": str(e), "raw": None}


async def main():
    ips = sys.argv[1:] if len(sys.argv) > 1 else ["10.102.1.64", "10.102.1.58", "10.102.1.59"]
    
    print("=" * 60)
    print("矿机 API 诊断 - 检查 summary 原始响应")
    print("=" * 60)
    
    for ip in ips:
        print(f"\n>>> 矿机: {ip}")
        result = await raw_summary(ip)
        
        if result["success"] and result["raw"]:
            raw = result["raw"]
            summary = raw.get("SUMMARY", [{}])[0] if raw.get("SUMMARY") else {}
            
            print("  原始 SUMMARY 键:", list(summary.keys()) if summary else "(空)")
            
            # 算力相关字段（不同固件可能用不同名称）
            hashrate_fields = ["GHS av", "GHS 5s", "GHS 15m", "MHS av", "MHS 5s", 
                               "Hashrate", "hashrate", "GH Safrompool", "GH/s"]
            found_hashrate = []
            for f in hashrate_fields:
                v = summary.get(f)
                if v is not None:
                    found_hashrate.append((f, v))
            if found_hashrate:
                print("  算力字段:", found_hashrate)
            else:
                print("  ⚠ 未找到已知算力字段! 完整 summary:", json.dumps(summary, ensure_ascii=False, indent=2)[:500])
            
            # STATUS
            status = raw.get("STATUS", [{}])[0] if raw.get("STATUS") else {}
            print("  STATUS:", status)
            
            # 固件/版本信息
            if "BMMiner" in str(summary) or "Type" in str(summary):
                print("  型号/固件相关:", {k: v for k, v in summary.items() if "miner" in k.lower() or "type" in k.lower() or "bm" in k.lower()})
        else:
            print("  ❌ 获取失败:", result.get("error", "无响应或解析失败"))
    
    print("\n" + "=" * 60)
    print("若某矿机「未找到已知算力字段」，说明其固件返回格式与标准不同，")
    print("需要根据上述 raw 输出调整 api_client_jsonrpc.py 的解析逻辑。")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
