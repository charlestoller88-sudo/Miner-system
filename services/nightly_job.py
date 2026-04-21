"""
每日定时：按网段扫描 → 矿池过滤 → 拉取全部命中矿机详细日志 → 与独立版相同规则的批量诊断报表。
不修改数据库中的矿机列表（避免 discover 接口「清空再导入」的副作用）。
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import config
from database.models import Miner, SessionLocal
from miners.log_fetcher import MinerLogFetcher
from services.local_ai_miner_diagnoser import run_batch_diagnosis
from utils.ip_scanner import discover_miners


def format_detailed_logs_for_diagnosis(
    miner_ip: str,
    serial: str,
    detailed: Dict[str, Any],
) -> str:
    """与 Web 导出格式一致，便于 local_ai_miner_diagnoser 解析 [SUMMARY]/[DEVS] 等。"""
    chunks: List[str] = []
    for rl in detailed.get("raw_logs") or []:
        cat = rl.get("category", "")
        body = rl.get("content", "")
        if cat and body:
            chunks.append(f"[{cat}]\n{body}")
    text = "\n".join(chunks)
    header_lines = [
        "=" * 60,
        f"矿机运行日志 - {miner_ip}",
        f"导出时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"IP: {miner_ip}  序列号: {serial}",
        "=" * 60,
        "",
    ]
    return "\n".join(header_lines) + text


async def run_nightly_pipeline(*, force: bool = False) -> Dict[str, Any]:
    """
    执行一轮：discover_miners（不写入 DB）→ 每台拉取 fetch_detailed_logs → 写入 txt → run_batch_diagnosis。
    force=True：忽略 NIGHTLY_JOB.enabled，供手动触发 /api/nightly/run-now。
    """
    cfg = getattr(config, "NIGHTLY_JOB", None) or {}
    if not force and not cfg.get("enabled"):
        return {"success": True, "skipped": True, "message": "NIGHTLY_JOB.enabled 为 False"}

    ip_ranges = cfg.get("ip_ranges")
    if ip_ranges is None:
        ip_ranges = ",".join(getattr(config, "IP_RANGES", []) or [])
    elif isinstance(ip_ranges, list):
        ip_ranges = ",".join(str(x) for x in ip_ranges)
    ip_ranges = (ip_ranges or "").strip()
    if not ip_ranges:
        return {"success": False, "message": "未配置网段（config.IP_RANGES 或 NIGHTLY_JOB.ip_ranges）"}

    pool_filter = (cfg.get("pool_filter") or "").strip() or None
    prefix = (cfg.get("output_prefix") or "low_hashrate_ai_report").strip() or "low_hashrate_ai_report"
    concurrency = max(1, int(cfg.get("log_concurrency", 8)))

    print(f"[NIGHTLY] 开始：网段={ip_ranges!r} 矿池过滤={pool_filter!r}")

    disc = await discover_miners(ip_range=ip_ranges, pool_filter=pool_filter)
    if not disc.get("success"):
        return {
            "success": False,
            "message": disc.get("error", "扫描失败"),
            "discovered": [],
        }

    discovered = disc.get("discovered") or []
    day = datetime.now().strftime("%Y-%m-%d")
    work_dir = config.BASE_DIR / "data" / "nightly_runs" / day
    work_dir.mkdir(parents=True, exist_ok=True)

    db = SessionLocal()
    fetcher = MinerLogFetcher(db)
    sem = asyncio.Semaphore(concurrency)
    ok_ips: List[str] = []
    errors: List[Dict[str, str]] = []

    async def fetch_one(item: Dict) -> None:
        ip = item.get("ip")
        if not ip:
            return
        serial = f"MINER-{ip.replace('.', '-')}"
        miner = Miner(
            ip_address=ip,
            serial_number=serial,
            model=item.get("model") or "Antminer",
            location=item.get("miner_name"),
        )
        async with sem:
            try:
                detailed = await fetcher.fetch_detailed_logs(miner)
                body = format_detailed_logs_for_diagnosis(ip, serial, detailed)
                safe = ip.replace(".", "_")
                out_path = work_dir / f"logs_{safe}.txt"
                out_path.write_text(body, encoding="utf-8")
                ok_ips.append(ip)
                print(f"[NIGHTLY] 已保存日志: {out_path.name}")
            except Exception as e:
                err = str(e)
                errors.append({"ip": ip, "error": err})
                print(f"[NIGHTLY] 拉取失败 {ip}: {err}")

    try:
        await asyncio.gather(*[fetch_one(d) for d in discovered])
    finally:
        db.close()

    diag: Dict[str, Any] = {}
    try:
        diag = await asyncio.to_thread(run_batch_diagnosis, work_dir, prefix)
    except Exception as e:
        print(f"[NIGHTLY] 诊断报表生成失败: {e}")
        diag = {"success": False, "error": str(e)}

    print(
        f"[NIGHTLY] 完成：发现 {len(discovered)} 台，日志成功 {len(ok_ips)}，失败 {len(errors)}，目录 {work_dir}"
    )

    return {
        "success": True,
        "work_dir": str(work_dir),
        "discovered_count": len(discovered),
        "logs_saved": len(ok_ips),
        "errors": errors,
        "diagnosis": diag,
    }


async def nightly_scheduler_loop() -> None:
    """在每天指定时刻执行一次 run_nightly_pipeline（本地时间）。"""
    cfg = getattr(config, "NIGHTLY_JOB", None) or {}
    hour = int(cfg.get("hour", 0))
    minute = int(cfg.get("minute", 0))

    while True:
        try:
            now = datetime.now()
            target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if target <= now:
                target = target + timedelta(days=1)
            wait_sec = max(1.0, (target - now).total_seconds())
            print(f"[NIGHTLY] 调度器：下次执行 {target.strftime('%Y-%m-%d %H:%M:%S')}（约 {int(wait_sec)}s 后）")
            await asyncio.sleep(wait_sec)
            await run_nightly_pipeline()
            await asyncio.sleep(90)
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"[NIGHTLY] 调度循环异常: {e}")
            import traceback

            traceback.print_exc()
            await asyncio.sleep(300)
