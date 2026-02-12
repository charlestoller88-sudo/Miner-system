import pandas as pd
from sqlalchemy.orm import Session
from database.models import Miner, get_db
from datetime import datetime

def load_miners_from_excel(excel_path: str, db: Session):
    """
    从Excel文件加载矿机信息到数据库
    """
    try:
        # 读取Excel文件
        df = pd.read_excel(excel_path, sheet_name='矿机明细')
        
        # 清理数据：去除空行，填充空值
        df = df.dropna(subset=['设备编号'])
        df['IP地址'] = df['IP地址'].fillna('')
        
        imported_count = 0
        skipped_count = 0
        
        for _, row in df.iterrows():
            serial_number = str(row['设备编号']).strip()
            ip_address = str(row['IP地址']).strip() if pd.notna(row['IP地址']) else ''
            
            # 检查是否已存在
            existing = db.query(Miner).filter(
                (Miner.serial_number == serial_number) | 
                (Miner.ip_address == ip_address and ip_address != '')
            ).first()
            
            if existing:
                skipped_count += 1
                continue
            
            # 根据设备编号推断型号
            model = 'Antminer S19 XP'  # 默认型号
            if 'NCATX' in serial_number:
                model = 'Antminer S19 XP'
            
            # 创建矿机记录
            miner = Miner(
                serial_number=serial_number,
                ip_address=ip_address if ip_address else None,
                model=model,
                status='offline' if ip_address else 'unknown',
                last_seen=datetime.now()
            )
            
            db.add(miner)
            imported_count += 1
        
        db.commit()
        
        return {
            'success': True,
            'imported': imported_count,
            'skipped': skipped_count,
            'total': len(df)
        }
        
    except Exception as e:
        db.rollback()
        return {
            'success': False,
            'error': str(e)
        }