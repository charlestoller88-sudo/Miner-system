from fastapi import FastAPI, Request, Depends, Form, UploadFile, File, Body
import tempfile
import shutil
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List
from sqlalchemy import func
from datetime import datetime, timedelta
import asyncio
import json
from pathlib import Path
from contextlib import asynccontextmanager
import io
import zipfile

from database.models import get_db, init_db, Miner, MinerStat, Alert, MinerLog
from miners.scanner import MinerScanner
from miners.analyzer import MinerAnalyzer
from miners.log_fetcher import MinerLogFetcher
from miners.api_client_jsonrpc import AntminerAPIJsonRPC
from utils.excel_loader import load_miners_from_excel
import config
import os

# 确保必要的目录存在
os.makedirs("static/css", exist_ok=True)
os.makedirs("static/js", exist_ok=True)
os.makedirs("static/images", exist_ok=True)
os.makedirs("templates", exist_ok=True)
os.makedirs("data/uploads", exist_ok=True)
os.makedirs("data/logs", exist_ok=True)
os.makedirs("data/nightly_runs", exist_ok=True)

_nightly_scheduler_task = None

# 生命周期管理
@asynccontextmanager
async def lifespan(app: FastAPI):
    global _nightly_scheduler_task
    print("矿机管理平台启动中...")
    init_db()
    nj = getattr(config, "NIGHTLY_JOB", None) or {}
    if nj.get("enabled"):
        from services.nightly_job import nightly_scheduler_loop

        _nightly_scheduler_task = asyncio.create_task(nightly_scheduler_loop())
        print("[NIGHTLY] 已启用每日定时任务（见 config.NIGHTLY_JOB）")
    yield
    if _nightly_scheduler_task:
        _nightly_scheduler_task.cancel()
        try:
            await _nightly_scheduler_task
        except asyncio.CancelledError:
            pass
    print("矿机管理平台关闭中...")

# 创建应用时指定生命周期 - 只创建一次！
app = FastAPI(
    title="矿机管理平台", 
    version="1.0.0",
    lifespan=lifespan
)

# 挂载静态文件
app.mount("/static", StaticFiles(directory="static"), name="static")

# 模板引擎
templates = Jinja2Templates(directory="templates")

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, db: Session = Depends(get_db)):
    """仪表板页面"""
    try:
        # 获取统计数据
        total_miners = db.query(Miner).count()
        online_miners = db.query(Miner).filter(Miner.status == 'online').count()
        offline_miners = db.query(Miner).filter(Miner.status == 'offline').count()
        error_miners = db.query(Miner).filter(Miner.status == 'error').count()
        
        # 获取最近的告警
        recent_alerts = db.query(Alert).filter(
            Alert.resolved == False
        ).order_by(Alert.created_at.desc()).limit(10).all()
        
        # 获取活跃矿机的算力总和和故障矿机数
        total_hashrate = 0
        zero_hashrate_miners = 0  # 0算力的故障矿机
        
        # 获取每个在线矿机的最新状态
        online_miner_list = db.query(Miner).filter(Miner.status == 'online').all()
        
        for miner in online_miner_list:
            latest_stat = db.query(MinerStat).filter(
                MinerStat.miner_id == miner.id
            ).order_by(MinerStat.timestamp.desc()).first()
            
            if latest_stat:
                hashrate = latest_stat.hashrate or 0
                if hashrate > 0:
                    total_hashrate += hashrate
                else:
                    # 算力为 0 的故障矿机
                    zero_hashrate_miners += 1
            else:
                # 没有统计数据的也算故障
                zero_hashrate_miners += 1
        
        # 获取矿机列表及其最新状态，默认按算力从高到低排序
        miners = []
        miner_query = db.query(Miner).all()
        
        for miner in miner_query:
            # 获取矿机最新状态
            latest_stat = db.query(MinerStat).filter(
                MinerStat.miner_id == miner.id
            ).order_by(MinerStat.timestamp.desc()).first()
            
            miners.append({
                'id': miner.id,
                'serial_number': miner.serial_number,
                'ip_address': miner.ip_address,
                'model': miner.model,
                'status': miner.status,
                'last_seen': miner.last_seen,
                'latest_stat': latest_stat
            })
        
        # 按算力从高到低排序（无数据/0算力的排最后）
        miners.sort(key=lambda m: (m['latest_stat'].hashrate or 0) if m['latest_stat'] else 0, reverse=True)
        
        context = {
            "request": request,
            "total_miners": total_miners,
            "online_miners": online_miners,
            "offline_miners": offline_miners,
            "error_miners": error_miners,
            "online_rate": round(online_miners / total_miners * 100, 1) if total_miners > 0 else 0,
            "total_hashrate": round(total_hashrate, 2),
            "zero_hashrate_miners": zero_hashrate_miners,  # 新增：0算力故障矿机数
            "recent_alerts": recent_alerts,
            "miners": miners,
        }
        
        return templates.TemplateResponse("dashboard.html", context)
        
    except Exception as e:
        print(f"仪表板页面错误: {str(e)}")
        return templates.TemplateResponse("error.html", {
            "request": request,
            "error": f"加载仪表板时出错: {str(e)}"
        })

@app.get("/statistics", response_class=HTMLResponse)
async def statistics_page(request: Request):
    """统计报表页面"""
    return templates.TemplateResponse("statistics.html", {"request": request})

@app.get("/miner/{miner_id}/logs", response_class=HTMLResponse)
async def miner_logs_page(request: Request, miner_id: int, db: Session = Depends(get_db)):
    """矿机原始日志页面"""
    try:
        miner = db.query(Miner).filter(Miner.id == miner_id).first()
        
        if not miner:
            return templates.TemplateResponse("error.html", {
                "request": request,
                "error": "矿机不存在"
            })
        
        return templates.TemplateResponse("miner_logs.html", {
            "request": request,
            "miner": miner
        })
    except Exception as e:
        print(f"日志页面错误: {str(e)}")
        return templates.TemplateResponse("error.html", {
            "request": request,
            "error": f"加载日志页面时出错: {str(e)}"
        })

@app.get("/miner/{miner_id}", response_class=HTMLResponse)
async def miner_detail(request: Request, miner_id: int, db: Session = Depends(get_db)):
    """矿机详情页面"""
    try:
        miner = db.query(Miner).filter(Miner.id == miner_id).first()
        
        if not miner:
            return templates.TemplateResponse("error.html", {
                "request": request,
                "error": "矿机不存在"
            })
        
        # 获取最新状态
        latest_stat = db.query(MinerStat).filter(
            MinerStat.miner_id == miner_id
        ).order_by(MinerStat.timestamp.desc()).first()
        
        # 获取历史记录（最近24小时）
        cutoff_time = datetime.now() - timedelta(hours=24)
        history_stats = db.query(MinerStat).filter(
            MinerStat.miner_id == miner_id,
            MinerStat.timestamp >= cutoff_time
        ).order_by(MinerStat.timestamp).all()
        
        # 获取相关告警
        alerts = db.query(Alert).filter(
            Alert.miner_id == miner_id,
            Alert.resolved == False
        ).order_by(Alert.created_at.desc()).all()
        
        # 分析矿机性能
        analyzer = MinerAnalyzer(db)
        analysis = analyzer.analyze_low_hashrate(miner_id)
        
        # 获取趋势数据
        try:
            trend_data = analyzer.get_miner_performance_trend(miner_id, 1)  # 最近1天
        except:
            trend_data = []
        
        context = {
            "request": request,
            "miner": miner,
            "latest_stat": latest_stat,
            "history_stats": history_stats,
            "alerts": alerts,
            "analysis": analysis,
            "trend_data": json.dumps(trend_data),
        }
        
        return templates.TemplateResponse("miner_detail.html", context)
        
    except Exception as e:
        print(f"矿机详情页面错误: {str(e)}")
        return templates.TemplateResponse("error.html", {
            "request": request,
            "error": f"加载矿机详情时出错: {str(e)}"
        })

@app.post("/scan")
async def scan_miners(db: Session = Depends(get_db)):
    """扫描所有矿机"""
    try:
        scanner = MinerScanner(db)
        result = await scanner.scan_all_miners()
        
        return {
            "success": True,
            "message": "扫描完成",
            "data": result
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"扫描失败: {str(e)}"
        }

@app.post("/api/scan-discover")
async def scan_discover_miners(
    ip_range: str = Body(..., embed=True),
    pool_filter: str = Body("", embed=True),
    db: Session = Depends(get_db)
):
    """
    IP范围扫描发现矿机
    ip_range: 支持 CIDR(10.102.0.0/24)、范围(10.102.0.1-10.102.0.100)、多个用逗号分隔
    pool_filter: 矿池URL关键词，留空则不过滤
    """
    try:
        scanner = MinerScanner(db)
        result = await scanner.discover_miners(
            ip_range=ip_range.strip(),
            pool_filter=pool_filter.strip() if pool_filter else None
        )
        return result
    except Exception as e:
        return {
            "success": False,
            "discovered": [],
            "total_ips": 0,
            "count": 0,
            "imported": 0,
            "skipped": 0,
            "error": str(e)
        }


@app.post("/import-excel")
async def import_excel(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """导入Excel文件"""
    try:
        if not file.filename.endswith('.xlsx'):
            return {"success": False, "message": "请上传Excel文件 (.xlsx)"}
        
        # 保存上传的文件
        upload_dir = Path("data/uploads")
        upload_dir.mkdir(exist_ok=True)
        
        file_path = upload_dir / f"upload_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        
        with open(file_path, "wb") as f:
            content = await file.read()
            f.write(content)
        
        # 加载数据
        result = load_miners_from_excel(str(file_path), db)
        
        return result
        
    except Exception as e:
        return {"success": False, "message": f"导入失败: {str(e)}"}

@app.post("/alert/{alert_id}/resolve")
async def resolve_alert(alert_id: int, db: Session = Depends(get_db)):
    """解决告警"""
    try:
        alert = db.query(Alert).filter(Alert.id == alert_id).first()
        
        if alert:
            alert.resolved = True
            alert.resolved_at = datetime.now()
            alert.resolved_by = "system"
            db.commit()
            
            return {"success": True, "message": "告警已解决"}
        
        return {"success": False, "message": "告警不存在"}
        
    except Exception as e:
        return {"success": False, "message": f"解决告警失败: {str(e)}"}

@app.get("/api/miners")
async def get_miners_api(db: Session = Depends(get_db)):
    """获取矿机列表API"""
    try:
        miners = db.query(Miner).all()
        
        result = []
        for miner in miners:
            latest_stat = db.query(MinerStat).filter(
                MinerStat.miner_id == miner.id
            ).order_by(MinerStat.timestamp.desc()).first()
            
            result.append({
                "id": miner.id,
                "ip_address": miner.ip_address,
                "serial_number": miner.serial_number,
                "model": miner.model,
                "status": miner.status,
                "last_seen": miner.last_seen.isoformat() if miner.last_seen else None,
                "hashrate": latest_stat.hashrate if latest_stat else 0,
                "temperature": latest_stat.temperature if latest_stat else 0,
                "power_usage": latest_stat.power_usage if latest_stat else 0,
            })
        
        return {"miners": result}
        
    except Exception as e:
        return {"error": str(e)}


class BatchPoolConfig(BaseModel):
    miner_ids: List[int]
    pool_url: str
    worker: str
    password: str = "x"


@app.post("/api/miners/batch-set-pool")
async def batch_set_pool(
    config_body: BatchPoolConfig,
    db: Session = Depends(get_db),
):
    """
    批量设置矿池配置（优先针对 bmminer 兼容固件，通过 4028 JSON-RPC addpool）
    """
    try:
        if not config_body.miner_ids:
            return {"success": False, "message": "请至少选择一台矿机"}

        miners = db.query(Miner).filter(Miner.id.in_(config_body.miner_ids)).all()
        if not miners:
            return {"success": False, "message": "未找到任何矿机"}

        success_list = []
        failed_list = []

        for miner in miners:
            if not miner.ip_address:
                failed_list.append(
                    {"id": miner.id, "ip": None, "reason": "矿机无 IP 地址"}
                )
                continue

            try:
                api = AntminerAPIJsonRPC(miner.ip_address)
                ok = await set_miner_pool_via_jsonrpc(
                    api,
                    pool_url=config_body.pool_url,
                    worker=config_body.worker,
                    password=config_body.password or "x",
                )
                if ok:
                    success_list.append({"id": miner.id, "ip": miner.ip_address})
                else:
                    failed_list.append(
                        {
                            "id": miner.id,
                            "ip": miner.ip_address,
                            "reason": "矿机拒绝或命令失败（请检查 API 权限/固件兼容性）",
                        }
                    )
            except Exception as e:
                failed_list.append(
                    {"id": miner.id, "ip": miner.ip_address, "reason": str(e)}
                )

        return {
            "success": True,
            "total": len(miners),
            "success_count": len(success_list),
            "failed_count": len(failed_list),
            "success_list": success_list,
            "failed_list": failed_list,
        }
    except Exception as e:
        return {"success": False, "message": str(e)}


class SinglePoolConfig(BaseModel):
    pool_url: str
    worker: str
    password: str = "x"


class UpdatePoolConfig(SinglePoolConfig):
    index: int


class DeletePoolConfig(BaseModel):
    index: int


@app.post("/api/miner/{miner_id}/pool/add")
async def add_single_pool(
    miner_id: int,
    body: SinglePoolConfig,
    db: Session = Depends(get_db),
):
    """单台矿机：新增矿池（bmminer JSON-RPC addpool）"""
    miner = db.query(Miner).filter(Miner.id == miner_id).first()
    if not miner:
        return {"success": False, "message": "矿机不存在"}
    if not miner.ip_address:
        return {"success": False, "message": "矿机无 IP 地址"}

    try:
        api = AntminerAPIJsonRPC(miner.ip_address)
        ok = await set_miner_pool_via_jsonrpc(
            api,
            pool_url=body.pool_url,
            worker=body.worker,
            password=body.password or "x",
        )
        if ok:
            return {"success": True, "message": "新增矿池成功（bmminer addpool）"}
        return {
            "success": False,
            "message": "矿机返回失败，请检查 API 权限/固件是否支持 addpool",
        }
    except Exception as e:
        return {"success": False, "message": str(e)}


@app.post("/api/miner/{miner_id}/pool/update")
async def update_single_pool(
    miner_id: int,
    body: UpdatePoolConfig,
    db: Session = Depends(get_db),
):
    """
    单台矿机：修改指定索引的矿池
    实现方式：先 removepool(index)，再 addpool(新配置)
    """
    miner = db.query(Miner).filter(Miner.id == miner_id).first()
    if not miner:
        return {"success": False, "message": "矿机不存在"}
    if not miner.ip_address:
        return {"success": False, "message": "矿机无 IP 地址"}

    try:
        api = AntminerAPIJsonRPC(miner.ip_address)
        # 先删除原有矿池
        removed = await remove_miner_pool_via_jsonrpc(api, pool_index=body.index)
        if not removed:
            # 不直接失败，部分固件可能不支持 removepool，对用户给出提示
            print(f"[WARN] {miner.ip_address} removepool 失败，继续尝试 addpool 覆盖")

        # 再新增新的矿池配置
        ok = await set_miner_pool_via_jsonrpc(
            api,
            pool_url=body.pool_url,
            worker=body.worker,
            password=body.password or "x",
        )
        if ok:
            return {"success": True, "message": "修改矿池成功"}
        return {
            "success": False,
            "message": "矿机返回失败，请检查 API 权限/固件是否支持 removepool/addpool",
        }
    except Exception as e:
        return {"success": False, "message": str(e)}


@app.post("/api/miner/{miner_id}/pool/delete")
async def delete_single_pool(
    miner_id: int,
    body: DeletePoolConfig,
    db: Session = Depends(get_db),
):
    """单台矿机：删除指定索引的矿池（bmminer JSON-RPC removepool）"""
    miner = db.query(Miner).filter(Miner.id == miner_id).first()
    if not miner:
        return {"success": False, "message": "矿机不存在"}
    if not miner.ip_address:
        return {"success": False, "message": "矿机无 IP 地址"}

    try:
        api = AntminerAPIJsonRPC(miner.ip_address)
        ok = await remove_miner_pool_via_jsonrpc(api, pool_index=body.index)
        if ok:
            return {"success": True, "message": "删除矿池成功"}
        return {
            "success": False,
            "message": "矿机返回失败，请检查 API 权限/固件是否支持 removepool",
        }
    except Exception as e:
        return {"success": False, "message": str(e)}

@app.get("/api/stats/{miner_id}")
async def get_miner_stats(miner_id: int, hours: int = 24, db: Session = Depends(get_db)):
    """获取矿机统计API"""
    try:
        cutoff_time = datetime.now() - timedelta(hours=hours)
        
        stats = db.query(MinerStat).filter(
            MinerStat.miner_id == miner_id,
            MinerStat.timestamp >= cutoff_time
        ).order_by(MinerStat.timestamp).all()
        
        return {
            "miner_id": miner_id,
            "stats": [
                {
                    "timestamp": s.timestamp.isoformat(),
                    "hashrate": s.hashrate,
                    "temperature": s.temperature,
                    "power_usage": s.power_usage,
                    "fan_speed": s.fan_speed,
                }
                for s in stats
            ]
        }
    except Exception as e:
        return {"error": str(e)}

# 在 app.py 中添加以下路由

@app.get("/api/miner/{miner_id}/stats")
async def get_miner_detailed_stats(miner_id: int, hours: int = 24, db: Session = Depends(get_db)):
    """获取矿机详细统计数据"""
    try:
        cutoff_time = datetime.now() - timedelta(hours=hours)
        
        stats = db.query(MinerStat).filter(
            MinerStat.miner_id == miner_id,
            MinerStat.timestamp >= cutoff_time
        ).order_by(MinerStat.timestamp).all()
        
        return {
            "success": True,
            "miner_id": miner_id,
            "stats": [
                {
                    "timestamp": s.timestamp.isoformat(),
                    "hashrate": s.hashrate,
                    "temperature": s.temperature,
                    "power_usage": s.power_usage,
                    "fan_speed": s.fan_speed,
                    "hw_errors": s.hw_errors,
                    "pool": s.pool,
                    "uptime": s.uptime,
                }
                for s in stats
            ]
        }
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.post("/api/miner/{miner_id}/force-scan")
async def force_scan_miner(miner_id: int, db: Session = Depends(get_db)):
    """强制扫描单个矿机"""
    try:
        miner = db.query(Miner).filter(Miner.id == miner_id).first()
        
        if not miner or not miner.ip_address:
            return {"success": False, "message": "矿机不存在或无IP地址"}
        
        # 创建扫描器
        scanner = MinerScanner(db)
        
        # 扫描单个矿机
        data = await scanner.scan_single_miner(miner)
        
        if data:
            return {
                "success": True,
                "message": "扫描成功",
                "data": data
            }
        else:
            return {
                "success": False,
                "message": "扫描失败，矿机可能离线"
            }
            
    except Exception as e:
        return {"success": False, "message": f"扫描失败: {str(e)}"}

@app.post("/api/miner/{miner_id}/restart")
async def restart_miner(miner_id: int, db: Session = Depends(get_db)):
    """重启矿机（模拟功能）"""
    try:
        miner = db.query(Miner).filter(Miner.id == miner_id).first()
        
        if not miner:
            return {"success": False, "message": "矿机不存在"}
        
        # 这里应该调用矿机的重启API
        # 暂时先模拟成功
        print(f"模拟重启矿机: {miner.ip_address}")
        
        return {
            "success": True,
            "message": "重启命令已发送",
            "data": {
                "miner_id": miner_id,
                "ip": miner.ip_address,
                "timestamp": datetime.now().isoformat()
            }
        }
        
    except Exception as e:
        return {"success": False, "message": f"重启失败: {str(e)}"}

@app.get("/api/miner/{miner_id}/logs")
async def get_miner_logs(miner_id: int, limit: int = 50, db: Session = Depends(get_db)):
    """获取矿机日志"""
    try:
        # 从数据库获取已有日志
        logs = db.query(MinerLog).filter(
            MinerLog.miner_id == miner_id
        ).order_by(MinerLog.timestamp.desc()).limit(limit).all()
        
        result = []
        for log in logs:
            result.append({
                'id': log.id,
                'timestamp': log.timestamp.isoformat(),
                'log_type': log.log_type,
                'content': log.content,
                'analyzed': log.analyzed,
                'analysis_result': log.analysis_result
            })
        
        return {
            "success": True,
            "count": len(result),
            "logs": result
        }
    except Exception as e:
        return {"success": False, "message": f"获取日志失败: {str(e)}"}

@app.post("/api/miner/{miner_id}/fetch-logs")
async def fetch_miner_logs(miner_id: int, db: Session = Depends(get_db)):
    """实时获取矿机日志"""
    try:
        miner = db.query(Miner).filter(Miner.id == miner_id).first()
        
        if not miner:
            return {"success": False, "message": "矿机不存在"}
        
        if not miner.ip_address:
            return {"success": False, "message": "矿机无IP地址"}
        
        # 获取日志
        log_fetcher = MinerLogFetcher(db)
        result = await log_fetcher.fetch_and_save_logs(miner)
        
        return result
        
    except Exception as e:
        return {"success": False, "message": f"获取日志失败: {str(e)}"}

@app.get("/api/miner/{miner_id}/detailed-info")
async def get_miner_detailed_info(miner_id: int, db: Session = Depends(get_db)):
    """获取矿机详细信息（矿池+算力板+原始日志）"""
    try:
        miner = db.query(Miner).filter(Miner.id == miner_id).first()
        
        if not miner:
            return {"success": False, "message": "矿机不存在"}
        
        if not miner.ip_address:
            return {"success": False, "message": "矿机无IP地址"}
        
        # 获取详细信息
        log_fetcher = MinerLogFetcher(db)
        result = await log_fetcher.fetch_detailed_logs(miner)
        
        return {
            "success": True,
            "miner_id": miner_id,
            "miner_ip": miner.ip_address,
            "pools": result['pools'],
            "boards": result['boards'],
            "raw_logs": result['raw_logs']
        }
        
    except Exception as e:
        return {"success": False, "message": f"获取详细信息失败: {str(e)}"}

@app.get("/api/statistics/overview")
async def get_statistics_overview(db: Session = Depends(get_db)):
    """获取统计概览数据"""
    try:
        print("[统计API] 开始获取统计数据...")
        
        # 基础统计
        total_miners = db.query(Miner).count()
        offline_miners = db.query(Miner).filter(Miner.status == 'offline').count()
        
        # 获取所有矿机，重新计算故障数（包括低算力/无算力）
        all_miners = db.query(Miner).all()
        
        # 定义故障阈值
        LOW_HASHRATE_THRESHOLD = config.THRESHOLDS.get('low_hashrate', 50)  # 默认 50 TH/s
        
        online_miners = 0
        error_miners = 0
        healthy_miners = 0  # 正常工作的矿机（在线且算力正常）
        
        for miner in all_miners:
            if miner.status == 'offline':
                continue  # 离线矿机单独统计
            
            # 获取最新算力数据
            latest_stat = db.query(MinerStat).filter(
                MinerStat.miner_id == miner.id
            ).order_by(MinerStat.timestamp.desc()).first()
            
            if latest_stat and latest_stat.hashrate is not None:
                hashrate = latest_stat.hashrate
                
                # 判断是否为故障（低算力或无算力）
                if hashrate < LOW_HASHRATE_THRESHOLD:
                    error_miners += 1
                else:
                    online_miners += 1
                    healthy_miners += 1
            else:
                # 没有算力数据的也算故障
                error_miners += 1
        
        print(f"[统计API] 矿机总数: {total_miners}, 正常: {healthy_miners}, 离线: {offline_miners}, 故障(含低算力): {error_miners}")
        
        # 计算百分比
        online_rate = round((healthy_miners / total_miners * 100), 1) if total_miners > 0 else 0
        offline_rate = round((offline_miners / total_miners * 100), 1) if total_miners > 0 else 0
        error_rate = round((error_miners / total_miners * 100), 1) if total_miners > 0 else 0
        
        # 统计型号分布并计算理论算力
        # all_miners 已在上面获取
        
        # 统计各型号数量
        model_stats = {}
        theoretical_hashrate = 0.0
        
        for miner in all_miners:
            model = miner.model or 'Antminer'
            model_stats[model] = model_stats.get(model, 0) + 1
            
            # 从配置获取该型号的理论算力
            hashrate = config.MODEL_HASHRATE.get(model, config.MODEL_HASHRATE.get('Antminer', 100.0))
            theoretical_hashrate += hashrate
        
        # 为了兼容前端，保留 s19xp_count
        s19xp_count = db.query(Miner).filter(Miner.model.like('%S19 XP%')).count()
        other_models_count = total_miners - s19xp_count
        
        # 计算实际算力（只统计健康矿机的算力）
        actual_hashrate = 0.0
        
        for miner in all_miners:
            if miner.status == 'offline':
                continue
            
            latest_stat = db.query(MinerStat).filter(
                MinerStat.miner_id == miner.id
            ).order_by(MinerStat.timestamp.desc()).first()
            
            if latest_stat and latest_stat.hashrate and latest_stat.hashrate >= LOW_HASHRATE_THRESHOLD:
                # 只累加算力正常的矿机
                actual_hashrate += latest_stat.hashrate
        
        print(f"[统计API] 理论算力: {theoretical_hashrate} TH/s, 实际算力: {actual_hashrate:.2f} TH/s")
        
        result = {
            "success": True,
            "total_miners": total_miners,
            "online_miners": healthy_miners,  # 正常工作的矿机数
            "offline_miners": offline_miners,
            "error_miners": error_miners,  # 包括低算力和无算力
            "online_rate": online_rate,
            "offline_rate": offline_rate,
            "error_rate": error_rate,
            "s19xp_count": s19xp_count,
            "other_models_count": other_models_count,
            "theoretical_hashrate": theoretical_hashrate,
            "actual_hashrate": actual_hashrate,
            "model_stats": model_stats,  # 各型号的统计数据
            "low_hashrate_threshold": LOW_HASHRATE_THRESHOLD  # 返回阈值供前端参考
        }
        
        print(f"[统计API] 返回数据: {result}")
        return result
        
    except Exception as e:
        print(f"[统计API] 错误: {str(e)}")
        import traceback
        traceback.print_exc()
        return {"success": False, "message": f"获取统计数据失败: {str(e)}"}

@app.post("/api/statistics/generate-report")
async def generate_statistics_report(hours: int = 12, db: Session = Depends(get_db)):
    """生成指定时间范围内的运行报告"""
    try:
        # 计算时间范围
        end_time = datetime.now()
        start_time = end_time - timedelta(hours=hours)
        
        # 获取所有矿机
        miners = db.query(Miner).all()
        
        # 汇总数据
        report_data = {
            "start_time": start_time.isoformat(),
            "end_time": end_time.isoformat(),
            "hours": hours,
            "summary": {
                "total_miners": len(miners),
                "avg_online_rate": 0,
                "avg_hashrate": 0,
                "total_power": 0,
                "total_alerts": 0,
                "fault_miners": 0
            },
            "miners": []
        }
        
        total_hashrate = 0
        total_power = 0
        fault_count = 0
        online_count = 0
        
        # 获取时间范围内的告警数
        alerts_count = db.query(Alert).filter(
            Alert.created_at >= start_time,
            Alert.created_at <= end_time
        ).count()
        
        report_data["summary"]["total_alerts"] = alerts_count
        
        # 分析每个矿机
        for miner in miners:
            # 获取该矿机在时间范围内的统计数据
            stats = db.query(MinerStat).filter(
                MinerStat.miner_id == miner.id,
                MinerStat.timestamp >= start_time,
                MinerStat.timestamp <= end_time
            ).all()
            
            if not stats:
                continue
            
            # 计算平均值
            avg_hashrate = sum([s.hashrate or 0 for s in stats]) / len(stats)
            avg_temperature = sum([s.temperature or 0 for s in stats]) / len(stats)
            avg_power = sum([s.power_usage or 0 for s in stats]) / len(stats)  # 修正：power_usage
            hw_errors = sum([s.hw_errors or 0 for s in stats])
            
            # 状态评估
            status_assessment = "正常"
            if avg_hashrate < 50:  # 算力低于 50 TH/s
                status_assessment = "异常"
                fault_count += 1
            elif avg_temperature > 75:  # 温度超过 75°C
                status_assessment = "警告"
            elif hw_errors > 100:  # 硬件错误过多
                status_assessment = "警告"
            
            if miner.status == 'online':
                online_count += 1
            
            total_hashrate += avg_hashrate
            total_power += avg_power
            
            miner_report = {
                "name": miner.location if miner.location else miner.serial_number,
                "ip": miner.ip_address,
                "avg_hashrate": avg_hashrate,
                "avg_temperature": avg_temperature,
                "avg_power": avg_power,
                "hw_errors": hw_errors,
                "data_points": len(stats),
                "status_assessment": status_assessment
            }
            
            report_data["miners"].append(miner_report)
        
        # 计算汇总统计
        if len(miners) > 0:
            report_data["summary"]["avg_online_rate"] = round((online_count / len(miners) * 100), 1)
            report_data["summary"]["avg_hashrate"] = total_hashrate
            report_data["summary"]["total_power"] = total_power
            report_data["summary"]["fault_miners"] = fault_count
        
        return {
            "success": True,
            "report": report_data
        }
        
    except Exception as e:
        return {"success": False, "message": f"生成报告失败: {str(e)}"}


@app.get("/api/statistics/fault-miners")
async def get_fault_miners_list(db: Session = Depends(get_db)):
    """获取当前零算力及低算力（<50 TH/s）矿机列表，用于统计页逐个下载日志"""
    try:
        low_threshold = float(config.THRESHOLDS.get("low_hashrate", 50))
        miners = db.query(Miner).filter(Miner.ip_address.isnot(None)).all()
        fault_list = []
        for m in miners:
            latest = (
                db.query(MinerStat)
                .filter(MinerStat.miner_id == m.id)
                .order_by(MinerStat.timestamp.desc())
                .first()
            )
            if latest is None or (latest.hashrate is None) or (float(latest.hashrate) < low_threshold):
                hashrate_val = latest.hashrate if latest and latest.hashrate is not None else None
                fault_list.append({
                    "id": m.id,
                    "ip_address": m.ip_address,
                    "serial_number": m.serial_number or "-",
                    "model": m.model or "-",
                    "hashrate": round(float(hashrate_val), 2) if hashrate_val is not None else None,
                })
        return {"success": True, "fault_miners": fault_list}
    except Exception as e:
        return {"success": False, "fault_miners": [], "message": str(e)}


@app.get("/api/statistics/export-fault-miner-logs", response_class=Response)
async def export_fault_miner_logs(miner_id: int = None, db: Session = Depends(get_db)):
    """导出指定矿机（零算力/低算力）的运行日志，单台下载。导出前会先实时拉取该矿机当日运行日志。"""
    if miner_id is None:
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": "请指定 miner_id，或在统计报表页从故障矿机列表中逐个下载"},
        )
    try:
        low_threshold = float(config.THRESHOLDS.get("low_hashrate", 50))
        miner = db.query(Miner).filter(Miner.id == miner_id).first()
        if not miner:
            return JSONResponse(status_code=404, content={"success": False, "message": "矿机不存在"})
        latest = (
            db.query(MinerStat)
            .filter(MinerStat.miner_id == miner.id)
            .order_by(MinerStat.timestamp.desc())
            .first()
        )
        if latest is not None and latest.hashrate is not None and float(latest.hashrate) >= low_threshold:
            return JSONResponse(
                status_code=400,
                content={"success": False, "message": "该矿机当前算力正常，仅支持导出故障矿机日志"},
            )
        if latest and latest.hashrate is not None:
            hashrate_str = f"{latest.hashrate:.2f} TH/s"
        else:
            hashrate_str = "无数据"

        # 导出前先实时拉取该矿机运行日志（与矿机详情页「刷新日志」同一逻辑）
        log_fetcher = MinerLogFetcher(db)
        fetch_result = await log_fetcher.fetch_and_save_logs(miner)
        logs_for_export = []
        if fetch_result.get("logs"):
            for log in fetch_result["logs"]:
                ts = log.get("timestamp")
                if hasattr(ts, "strftime"):
                    ts_str = ts.strftime("%Y-%m-%d %H:%M:%S")
                else:
                    ts_str = str(ts) if ts else ""
                logs_for_export.append({
                    "ts": ts_str,
                    "log_type": log.get("log_type") or "info",
                    "content": log.get("content") or "",
                    "analysis": log.get("analysis"),
                })
        if not logs_for_export:
            # 拉取失败或为空时，使用数据库中已有日志
            db_logs = (
                db.query(MinerLog)
                .filter(MinerLog.miner_id == miner.id)
                .order_by(MinerLog.timestamp.desc())
                .limit(100)
                .all()
            )
            for log in reversed(db_logs):
                ts = log.timestamp.strftime("%Y-%m-%d %H:%M:%S") if log.timestamp else ""
                logs_for_export.append({
                    "ts": ts,
                    "log_type": log.log_type or "info",
                    "content": log.content or "",
                    "analysis": log.analysis_result,
                })

        lines = [
            "=" * 60,
            f"故障矿机运行日志 - {miner.ip_address}",
            f"导出时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"IP: {miner.ip_address}  序列号: {miner.serial_number}  型号: {miner.model or '-'}  当前算力: {hashrate_str}",
            "=" * 60,
            "",
        ]
        if logs_for_export:
            for entry in logs_for_export:
                lines.append(f"[{entry['ts']}] [{entry['log_type']}] {entry['content']}")
                if entry.get("analysis"):
                    lines.append(f"  → 分析: {entry['analysis']}")
        else:
            lines.append("（实时拉取失败且无历史日志：矿机可能离线、固件不支持或网络不通，请检查 IP 与矿机状态后重试）")
        content = "\n".join(lines)
        safe_name = (miner.ip_address or "").replace(".", "_") + "_" + (miner.serial_number or str(miner.id))[:20]
        filename = f"fault_log_{safe_name}_{datetime.now().strftime('%Y%m%d_%H%M')}.txt"
        return Response(
            content=content.encode("utf-8"),
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"success": False, "message": str(e)},
        )


@app.get("/api/statistics/export-all-miner-logs", response_class=Response)
async def export_all_miner_logs(db: Session = Depends(get_db)):
    """
    批量导出当前所有有 IP 的矿机运行日志，打包为 zip。
    - 每台矿机优先实时拉取运行日志（与单台导出逻辑相同），失败时再用数据库已有 MinerLog 兜底。
    """
    miners = db.query(Miner).filter(Miner.ip_address.isnot(None)).all()
    if not miners:
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": "当前没有任何带 IP 的矿机"},
        )

    buf = io.BytesIO()
    zf = zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED)

    for miner in miners:
        try:
            logs_for_export = []

            # 尝试获取详细原始日志（包含 SYSTEM_LOG 等大文本），优先使用这一套
            detailed = None
            try:
                log_fetcher = MinerLogFetcher(db)
                detailed = await log_fetcher.fetch_detailed_logs(miner)
            except Exception as e:
                print(f"[EXPORT_LOGS] 获取矿机 {miner.id} 详细日志失败: {e}")

            if detailed and detailed.get("raw_logs"):
                for entry in detailed["raw_logs"]:
                    ts = entry.get("timestamp")
                    if hasattr(ts, "strftime"):
                        ts_str = ts.strftime("%Y-%m-%d %H:%M:%S")
                    else:
                        ts_str = str(ts) if ts else ""
                    cat = entry.get("category") or "RAW"
                    content = entry.get("content") or ""
                    desc = entry.get("description")
                    header = f"[{ts_str}] [{cat}]"
                    if desc:
                        header += f" {desc}"
                    logs_for_export.append(header)
                    logs_for_export.append(content)
                    logs_for_export.append("")  # 空行分隔不同块

            # 若 detailed/raw_logs 为空，再退回到数据库已有 MinerLog 记录
            if not logs_for_export:
                db_logs = (
                    db.query(MinerLog)
                    .filter(MinerLog.miner_id == miner.id)
                    .order_by(MinerLog.timestamp.desc())
                    .limit(200)
                    .all()
                )
                for log in reversed(db_logs):
                    ts = log.timestamp.strftime("%Y-%m-%d %H:%M:%S") if log.timestamp else ""
                    line = f"[{ts}] [{log.log_type or 'info'}] {log.content or ''}"
                    logs_for_export.append(line)
                    if log.analysis_result:
                        logs_for_export.append(f"  → 分析: {log.analysis_result}")
            lines = [
                "=" * 60,
                f"矿机运行日志 - {miner.ip_address}",
                f"导出时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                f"IP: {miner.ip_address}  序列号: {miner.serial_number}  型号: {miner.model or '-'}",
                "=" * 60,
                "",
            ]
            if logs_for_export:
                lines.extend(logs_for_export)
            else:
                lines.append("（实时拉取失败且无历史日志：矿机可能离线、固件不支持或网络不通，请检查 IP 与矿机状态后重试）")

            content = "\n".join(lines)
            safe_name = (miner.ip_address or "").replace(".", "_") + "_" + (miner.serial_number or str(miner.id))[:20]
            filename = f"logs_{safe_name}.txt"
            zf.writestr(filename, content)
        except Exception as e:
            print(f"[EXPORT_LOGS] 导出矿机 {miner.id} 日志失败: {e}")
            continue

    zf.close()
    buf.seek(0)
    zip_name = f"all_miner_logs_{datetime.now().strftime('%Y%m%d_%H%M')}.zip"
    return Response(
        content=buf.read(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename=\"{zip_name}\"'},
    )


def _flatten_txt_to_workdir(work_dir: Path) -> None:
    """将子目录中的 .txt 移到 work_dir 根目录（与独立版「按日期文件夹」解压结构兼容）。"""
    for p in list(work_dir.rglob("*.txt")):
        if p.parent == work_dir:
            continue
        dest = work_dir / p.name
        n = 0
        while dest.exists():
            n += 1
            dest = work_dir / f"{p.stem}_{n}{p.suffix}"
        shutil.move(str(p), str(dest))


@app.get("/log-ai-report", response_class=HTMLResponse)
async def log_ai_report_page(request: Request):
    """批量矿机日志 AI 诊断（与独立项目「AI诊断矿机运行日志报告」同源规则 + 可选 Ollama）。"""
    return templates.TemplateResponse("log_ai_report.html", {"request": request})


@app.post("/api/log-ai-report/run")
async def run_log_ai_report_batch(
    files: List[UploadFile] = File(...),
    output_prefix: str = Form("low_hashrate_ai_report"),
):
    """
    上传多个 *.txt 矿机日志，或上传包含日志的 *.zip（可含子目录）。
    生成 low_hashrate_ai_report.csv / .xlsx 并打包为 zip 下载。
    """
    from services.local_ai_miner_diagnoser import run_batch_diagnosis

    work = Path(tempfile.mkdtemp(prefix="log_ai_"))
    try:
        for uf in files:
            raw_name = uf.filename or "upload"
            safe = Path(raw_name).name
            data = await uf.read()
            if not data:
                continue
            if safe.lower().endswith(".zip"):
                zp = work / safe
                zp.write_bytes(data)
                with zipfile.ZipFile(zp, "r") as zf:
                    zf.extractall(work)
            else:
                if not safe.lower().endswith(".txt"):
                    safe = f"{safe}.txt"
                (work / safe).write_bytes(data)

        _flatten_txt_to_workdir(work)

        if not list(work.glob("*.txt")):
            return JSONResponse(
                status_code=400,
                content={"success": False, "message": "未找到有效的 .txt 日志文件（请直接上传 txt，或上传含 txt 的 zip）"},
            )

        result = run_batch_diagnosis(work, output_prefix=output_prefix.strip() or "low_hashrate_ai_report")

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            csv_p = Path(result["csv_path"])
            if csv_p.is_file():
                zf.write(csv_p, arcname=csv_p.name)
            xlsx_path = result.get("xlsx_path")
            if xlsx_path:
                xp = Path(xlsx_path)
                if xp.is_file():
                    zf.write(xp, arcname=xp.name)

        buf.seek(0)
        stamp = datetime.now().strftime("%Y%m%d_%H%M")
        zip_name = f"low_hashrate_ai_report_{stamp}.zip"
        return Response(
            content=buf.read(),
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="{zip_name}"',
                "X-Diagnosis-Rows": str(result.get("row_count", 0)),
                "X-Diagnosis-Llm-Calls": str(result.get("llm_used", 0)),
            },
        )
    except FileNotFoundError as e:
        return JSONResponse(status_code=400, content={"success": False, "message": str(e)})
    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "message": str(e)})
    finally:
        shutil.rmtree(work, ignore_errors=True)


@app.post("/api/nightly/run-now")
async def nightly_run_now():
    """
    立即执行一轮：按 NIGHTLY_JOB 的网段与矿池过滤扫描 → 拉取命中矿机详细日志 → 生成诊断报表。
    不检查 enabled（force=True），便于调试；输出目录为 data/nightly_runs/日期/。
    """
    from services.nightly_job import run_nightly_pipeline

    result = await run_nightly_pipeline(force=True)
    return result


# 创建简单的错误页面模板
@app.get("/error")
async def error_page(request: Request, message: str = "未知错误"):
    return templates.TemplateResponse("error.html", {
        "request": request,
        "error": message
    })

if __name__ == "__main__":
    import uvicorn
    
    # 检查必要的文件是否存在
    if not os.path.exists("static/css/style.css"):
        print("警告: static/css/style.css 文件不存在，正在创建默认样式...")
        # 创建默认样式文件
        os.makedirs("static/css", exist_ok=True)
        with open("static/css/style.css", "w", encoding="utf-8") as f:
            f.write("""/* 基础样式 */
* {
    margin: 0;
    padding: 0;
    box-sizing: border-box;
}

body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
    background-color: #f5f7fa;
    color: #333;
}

.container {
    max-width: 1400px;
    margin: 0 auto;
    padding: 20px;
}

/* 导航栏 */
.navbar {
    display: flex;
    justify-content: space-between;
    align-items: center;
    background: white;
    padding: 15px 25px;
    border-radius: 10px;
    box-shadow: 0 2px 10px rgba(0,0,0,0.1);
    margin-bottom: 30px;
}

.nav-brand {
    display: flex;
    align-items: center;
    gap: 10px;
    font-size: 1.5rem;
    font-weight: bold;
    color: #2c3e50;
}

.nav-brand i {
    color: #3498db;
}

.nav-menu {
    display: flex;
    gap: 25px;
}

.nav-menu a {
    text-decoration: none;
    color: #7f8c8d;
    padding: 8px 16px;
    border-radius: 6px;
    transition: all 0.3s;
}

.nav-menu a:hover,
.nav-menu a.active {
    background: #3498db;
    color: white;
}

.nav-actions {
    display: flex;
    gap: 10px;
}

/* 按钮样式 */
.btn {
    padding: 10px 20px;
    border: none;
    border-radius: 6px;
    cursor: pointer;
    font-weight: 500;
    transition: all 0.3s;
    display: inline-flex;
    align-items: center;
    gap: 8px;
}

.btn:hover {
    transform: translateY(-2px);
    box-shadow: 0 4px 12px rgba(0,0,0,0.15);
}

.btn-primary {
    background: #3498db;
    color: white;
}

.btn-primary:hover {
    background: #2980b9;
}

.btn-secondary {
    background: #2ecc71;
    color: white;
}

.btn-secondary:hover {
    background: #27ae60;
}

.btn-info {
    background: #17a2b8;
    color: white;
}

.btn-sm {
    padding: 6px 12px;
    font-size: 0.9rem;
}

/* 表格样式 */
.miner-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.9rem;
}

.miner-table th {
    background: #f8f9fa;
    padding: 15px;
    text-align: left;
    font-weight: 600;
    color: #2c3e50;
    border-bottom: 2px solid #e9ecef;
}

.miner-table td {
    padding: 15px;
    border-bottom: 1px solid #e9ecef;
}

.miner-table tr:hover {
    background: #f8f9fa;
}

/* 状态徽章 */
.status-badge {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 4px 12px;
    border-radius: 20px;
    font-size: 0.85rem;
    font-weight: 500;
}

.status-online {
    background: rgba(46, 204, 113, 0.1);
    color: #27ae60;
}

.status-offline {
    background: rgba(231, 76, 60, 0.1);
    color: #c0392b;
}

.status-unknown {
    background: rgba(149, 165, 166, 0.1);
    color: #7f8c8d;
}

/* 响应式设计 */
@media (max-width: 768px) {
    .navbar {
        flex-direction: column;
        gap: 15px;
    }
    
    .nav-menu {
        flex-wrap: wrap;
        justify-content: center;
    }
}""")
    
    if not os.path.exists("templates/error.html"):
        print("警告: templates/error.html 文件不存在，正在创建...")
        os.makedirs("templates", exist_ok=True)
        with open("templates/error.html", "w", encoding="utf-8") as f:
            f.write("""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>错误 - 矿机管理平台</title>
    <link rel="stylesheet" href="/static/css/style.css">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css">
</head>
<body>
    <div class="container">
        <div style="text-align: center; padding: 100px 20px;">
            <i class="fas fa-exclamation-triangle" style="font-size: 48px; color: #e74c3c; margin-bottom: 20px;"></i>
            <h1>出错了</h1>
            <p style="color: #7f8c8d; margin: 20px 0 30px 0;">{{ error }}</p>
            <a href="/" class="btn btn-primary">
                <i class="fas fa-home"></i> 返回首页
            </a>
        </div>
    </div>
</body>
</html>""")
    
    print("服务启动中...")
    print("访问地址: http://localhost:8000")
    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    )