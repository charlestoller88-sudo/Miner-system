from fastapi import FastAPI, Request, Depends, Form, UploadFile, File, Body
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import func, or_
from datetime import datetime, timedelta
import asyncio
import json
from pathlib import Path
from contextlib import asynccontextmanager

from database.models import get_db, init_db, Miner, MinerStat, Alert, MinerLog, MinerRawSnapshot, FaultLabel, AIDiagnosisFeedback
from miners.scanner import MinerScanner
from miners.analyzer import MinerAnalyzer
from miners.log_fetcher import MinerLogFetcher
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
os.makedirs("data/training_exports", exist_ok=True)

# 数据采集后台任务控制
_collector_task = None
_collector_stop = False

async def _data_collection_loop():
    """后台数据采集循环"""
    global _collector_stop
    from database.models import SessionLocal
    from services.data_collector import DataCollector
    
    interval = config.DATA_COLLECTION.get("interval_seconds", 300)
    print(f"[DATA_COLLECTOR] 数据采集服务已启动，间隔 {interval} 秒")
    
    while not _collector_stop:
        try:
            db = SessionLocal()
            collector = DataCollector(db)
            result = await collector.run_collection_cycle()
            db.close()
            print(f"[DATA_COLLECTOR] 采集完成: 成功 {result['success']}, 失败 {result['failed']}, 总计 {result['total']}")
        except Exception as e:
            print(f"[DATA_COLLECTOR] 采集异常: {e}")
        
        for _ in range(interval):
            if _collector_stop:
                break
            await asyncio.sleep(1)
    
    print("[DATA_COLLECTOR] 数据采集服务已停止")

# 生命周期管理
@asynccontextmanager
async def lifespan(app: FastAPI):
    global _collector_task, _collector_stop
    # 启动时
    print("矿机管理平台启动中...")
    # 初始化数据库
    init_db()
    
    _collector_stop = False
    _collector_task = asyncio.create_task(_data_collection_loop())
    
    yield
    
    # 关闭时
    _collector_stop = True
    if _collector_task:
        _collector_task.cancel()
        try:
            await _collector_task
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


# ========== AI 训练数据采集与标注 API ==========

@app.post("/api/collect-now")
async def trigger_data_collection(db: Session = Depends(get_db)):
    """手动触发一次数据采集"""
    try:
        from services.data_collector import DataCollector
        collector = DataCollector(db)
        result = await collector.run_collection_cycle()
        return {"success": True, "result": result}
    except Exception as e:
        return {"success": False, "message": str(e)}

def _five_min_bucket_key(ts) -> tuple:
    """5 分钟时间桶：(date, hour, minute//5)，用于同一 5 分钟内只保留一条"""
    if ts is None:
        return None
    if hasattr(ts, "date"):
        d, t = ts.date(), ts
    else:
        try:
            t = datetime.strptime(str(ts)[:19], "%Y-%m-%dT%H:%M:%S") if isinstance(ts, str) else ts
            d = t.date() if hasattr(t, "date") else t
        except Exception:
            return None
    return (d, t.hour, t.minute // 5)


@app.get("/api/miner/{miner_id}/snapshots")
async def get_miner_snapshots(
    miner_id: int,
    limit: int = 500,
    unlabeled_only: bool = True,
    fault_only: bool = True,
    db: Session = Depends(get_db),
):
    """
    获取矿机待标记故障快照（用于标注）。
    - 仅返回「故障」快照：算力低于 50 TH/s 或无算力（与 config.THRESHOLDS 一致）。
    - 只排除已标注过的快照 ID，不按日整日排除，以便当天新产生的故障快照仍会出现在清单中。
    - 同一矿机 5 分钟内只保留一条代表快照，避免 5 分钟内重复标记。
    - 按日期倒序排序：当天排最上，依次昨天、前天……（即 timestamp 倒序）。
    """
    try:
        low_threshold = float(config.THRESHOLDS.get("low_hashrate", 50))
        query = db.query(MinerRawSnapshot).filter(MinerRawSnapshot.miner_id == miner_id)

        if unlabeled_only:
            # 只排除已标注过的 snapshot_id，不按日排除，这样当天后续新产生的故障快照仍会显示
            labeled_ids = db.query(FaultLabel.snapshot_id).filter(
                FaultLabel.miner_id == miner_id,
                FaultLabel.snapshot_id.isnot(None),
            ).distinct().all()
            labeled_ids = [r[0] for r in labeled_ids if r[0]]
            if labeled_ids:
                query = query.filter(~MinerRawSnapshot.id.in_(labeled_ids))

        # 只保留故障快照：无算力或算力低于阈值
        if fault_only:
            query = query.filter(
                or_(
                    MinerRawSnapshot.hashrate.is_(None),
                    MinerRawSnapshot.hashrate < low_threshold,
                )
            )

        # 按时间倒序（当天在最上），多取一些再做 5 分钟去重
        candidates = (
            query.order_by(MinerRawSnapshot.timestamp.desc())
            .limit(limit * 2 + 500)
            .all()
        )

        # 同一矿机 5 分钟内只保留一条（保留该时间窗内最新的一条）
        seen_buckets = set()
        snapshots = []
        for s in candidates:
            if not s.timestamp:
                snapshots.append(s)
                if len(snapshots) >= limit:
                    break
                continue
            key = _five_min_bucket_key(s.timestamp)
            if key is None:
                continue
            if key not in seen_buckets:
                seen_buckets.add(key)
                snapshots.append(s)
                if len(snapshots) >= limit:
                    break

        snapshots.sort(key=lambda x: (x.timestamp or datetime.min), reverse=True)

        return {
            "success": True,
            "snapshots": [
                {
                    "id": s.id,
                    "timestamp": s.timestamp.isoformat() if s.timestamp else None,
                    "hashrate": s.hashrate,
                    "temperature": s.temperature,
                    "power_usage": s.power_usage,
                    "fan_speed": s.fan_speed,
                    "hw_errors": s.hw_errors,
                    "fault_type": s.fault_type,
                }
                for s in snapshots
            ]
        }
    except Exception as e:
        return {"success": False, "message": str(e)}

@app.post("/api/snapshot/{snapshot_id}/label")
async def label_snapshot(
    snapshot_id: int,
    body: dict = Body(...),
    db: Session = Depends(get_db)
):
    """标注快照的故障类型（单个）"""
    try:
        snapshot = db.query(MinerRawSnapshot).filter(MinerRawSnapshot.id == snapshot_id).first()
        if not snapshot:
            return {"success": False, "message": "快照不存在"}
        
        fault_type = body.get("fault_type", "")
        fault_cause = body.get("fault_cause", "") or None
        solution = body.get("solution", "") or None
        
        label = FaultLabel(
            miner_id=snapshot.miner_id,
            snapshot_id=snapshot_id,
            fault_type=fault_type,
            fault_cause=fault_cause,
            solution=solution,
        )
        db.add(label)
        db.commit()
        return {"success": True, "message": "标注成功"}
    except Exception as e:
        db.rollback()
        return {"success": False, "message": str(e)}


@app.post("/api/snapshot/batch-label")
async def batch_label_snapshots(
    body: dict = Body(...),
    db: Session = Depends(get_db)
):
    """批量标注多个快照（相同故障类型、原因、方案）"""
    try:
        snapshot_ids = body.get("snapshot_ids", [])
        if not snapshot_ids:
            return {"success": False, "message": "请至少选择一个快照"}
        
        fault_type = body.get("fault_type", "")
        fault_cause = body.get("fault_cause", "").strip() or None
        solution = body.get("solution", "").strip() or None
        
        snapshots = db.query(MinerRawSnapshot).filter(
            MinerRawSnapshot.id.in_(snapshot_ids)
        ).all()
        
        if len(snapshots) != len(snapshot_ids):
            return {"success": False, "message": "部分快照不存在"}
        
        for snapshot in snapshots:
            # 若已有标注则更新，否则新增
            existing = db.query(FaultLabel).filter(
                FaultLabel.snapshot_id == snapshot.id
            ).first()
            if existing:
                existing.fault_type = fault_type
                existing.fault_cause = fault_cause
                existing.solution = solution
            else:
                label = FaultLabel(
                    miner_id=snapshot.miner_id,
                    snapshot_id=snapshot.id,
                    fault_type=fault_type,
                    fault_cause=fault_cause,
                    solution=solution,
                )
                db.add(label)
        
        db.commit()
        return {"success": True, "message": f"已成功标注 {len(snapshots)} 条快照"}
    except Exception as e:
        db.rollback()
        return {"success": False, "message": str(e)}


@app.get("/api/label-history")
async def get_label_history(db: Session = Depends(get_db)):
    """获取历史标注的可能原因和处理方案（用于快速填充，按最近使用排序）"""
    try:
        labels = db.query(FaultLabel).filter(
            (FaultLabel.fault_cause.isnot(None)) & (FaultLabel.fault_cause != "")
        ).order_by(FaultLabel.labeled_at.desc()).limit(100).all()
        causes = list(dict.fromkeys([l.fault_cause for l in labels if l.fault_cause]))[:30]
        
        labels2 = db.query(FaultLabel).filter(
            (FaultLabel.solution.isnot(None)) & (FaultLabel.solution != "")
        ).order_by(FaultLabel.labeled_at.desc()).limit(100).all()
        solutions = list(dict.fromkeys([l.solution for l in labels2 if l.solution]))[:30]
        
        return {"success": True, "fault_causes": causes, "solutions": solutions}
    except Exception as e:
        return {"success": False, "fault_causes": [], "solutions": []}

@app.post("/api/miner/{miner_id}/ai-diagnose")
async def ai_diagnose_miner(miner_id: int, db: Session = Depends(get_db)):
    """AI 诊断矿机（模型未部署时使用规则引擎）"""
    try:
        snapshot = db.query(MinerRawSnapshot).filter(
            MinerRawSnapshot.miner_id == miner_id
        ).order_by(MinerRawSnapshot.timestamp.desc()).first()
        
        if not snapshot:
            return {"success": False, "message": "暂无快照数据，请先执行数据采集"}
        
        from services.ai_diagnoser import diagnose_miner
        result = diagnose_miner(snapshot, db)
        return {"success": True, "diagnosis": result}
    except Exception as e:
        return {"success": False, "message": str(e)}

@app.post("/api/ai-diagnosis/feedback")
async def ai_diagnosis_feedback(body: dict = Body(...), db: Session = Depends(get_db)):
    """AI 诊断用户反馈"""
    try:
        feedback = AIDiagnosisFeedback(
            snapshot_id=body.get("snapshot_id"),
            miner_id=body.get("miner_id"),
            ai_fault_type=body.get("ai_fault_type"),
            ai_confidence=body.get("ai_confidence"),
            user_correct=body.get("user_correct"),
            user_actual_fault_type=body.get("user_actual_fault_type"),
        )
        db.add(feedback)
        db.commit()
        return {"success": True}
    except Exception as e:
        db.rollback()
        return {"success": False, "message": str(e)}

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