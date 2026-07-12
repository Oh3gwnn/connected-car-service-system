import time
import uuid
from fastapi import FastAPI, BackgroundTasks
from pydantic import BaseModel
import requests
import uvicorn

app = FastAPI(title="Automotive CCU Minimal Simulator")

# 런타임 하드웨어 레지스터 (초기 상태: SLEEP)
v_reg = {
    "power_state": "SLEEP",
    "panel_acc": 0,       # 내부 로깅용
    "wire_acc": 0,        # UI 및 VSS 검증용
    "ign1": 0,            # 디폴트 유지 대상 (0)
    "ign3": 0,            # 디폴트 유지 대상 (0)
    "sleepmode": 1,       # 1: SLEEP, 0: WAKEUP
    "door_locked": 1      # 1: LOCKED, 0: UNLOCKED (RDO 제어 대상)
}

TMS_SERVER_URL = "http://127.0.0.1:8000/api/v1/telematics/status"

class SignalPayload(BaseModel):
    signal: str          # WAKEUP_TRIGGER, BPLUS_RESET, TRIGGER_BALARM
    command: str = ""    # RDO, RSC (MT 제어용)

def execute_wakeup_sequence(tx_id: str):
    """[REQ-WAKEUP] 5초 부팅 지연 후 이더넷 활성화 및 토큰 발급, VSS 동기화"""
    global v_reg
    print("⏳ [BOOT] 가상 시스템 커널 부팅 중 (5초 지연 적용)...")
    time.sleep(5.0)
    
    v_reg["power_state"] = "READY"
    v_reg["sleepmode"] = 0  # WAKEUP 상태 천이
    print("🌐 [NETWORK] 이더넷 링크 체결 완료. MQTT 토큰 1개 발급 성공.")
    
    # [MO 통보 - VSS] sleepmode=0, acc=1, ign1/3=0(디폴트) 동기화 패킷 전송 (204)
    vss_msg = f"[VSS] sleepmode=0, acc=1, ign1={v_reg['ign1']}(Default), ign3={v_reg['ign3']}(Default)"
    send_to_tms(tx_id, 204, vss_msg)
    
    # 최종 부팅 성공 완료 통보 (200)
    send_to_tms(tx_id, 200, "RSC_WAKEUP_SUCCESS")

def send_to_tms(tx_id: str, status_code: int, message: str):
    """TMS 관제 서버로 트랜잭션 데이터 전송"""
    try:
        payload = {
            "transaction_id": tx_id,
            "vin": "KMHCT41BPJU123456",
            "status_code": status_code,
            "message": message,
            "vehicle_state": {"wires": {"acc": v_reg["wire_acc"], "ign1": v_reg["ign1"]}}
        }
        requests.post(TMS_SERVER_URL, json=payload, timeout=2)
        print(f"📡 [차량 ➔ 서버] 통보 성공 | Code {status_code} | Msg: {message}")
    except Exception:
        print(f"⚠️ [차량 ➔ 서버] 관제 서버(Port 8000) 미기동으로 로컬 터미널에만 로그 출력")

# --- 하드웨어 시그널 인터페이스 엔드포인트 ---
@app.post("/hardware/signal")
async def receive_hardware_signal(req: SignalPayload, bg_tasks: BackgroundTasks):
    global v_reg
    tx_id = f"tx-{str(uuid.uuid4())[:8]}"
    
    # 1. B+ 하드 리셋 (공장 초기화)
    if req.signal == "BPLUS_RESET":
        v_reg = {"power_state": "SLEEP", "panel_acc": 0, "wire_acc": 0, "ign1": 0, "ign3": 0, "sleepmode": 1, "door_locked": 1}
        print("⚡ [B+ RESET] 배터리 상시 전압 단절 후 모든 레지스터 디폴트 원복 완료.")
        return {"status": "RESET_COMPLETED"}
        
    # 2. 웨이크업 트리거 (Wire ACC + Panel ACC 동시 ON)
    elif req.signal == "WAKEUP_TRIGGER":
        v_reg["panel_acc"] = 1
        v_reg["wire_acc"] = 1
        v_reg["power_state"] = "BOOTING"
        print("📝 [LOG] Panel ACC = ON 감지 (내부 로깅용)")
        print("🔌 [WIRE] Wire ACC = ON 인가 완료 (대시보드 연동용)")
        bg_tasks.add_task(execute_wakeup_sequence, tx_id)
        return {"status": "TRANSITION_STARTED", "transaction_id": tx_id}
        
    # 3. MO 통보 트리거 - SVN (B-Alarm 도난 경보) 발생 상황
    elif req.signal == "TRIGGER_BALARM":
        print("🚨 [SECURITY] 차량 외부 충격 감지! 보안 침입 이벤트 기동.")
        # [MO 통보 - SVN] 도난 경보 신호 즉각 쏘아 올림 (상태코드 500 모사)
        send_to_tms(tx_id, 500, "SVN_SECURITY_ALARM_ACTIVE (B-Alarm)")
        return {"status": "SVN_SENT", "transaction_id": tx_id}

# --- MT 원격 제어 인터페이스 엔드포인트 (서버 ➔ 차량 명령) ---
@app.post("/hardware/remote")
async def receive_remote_command(req: SignalPayload):
    global v_reg
    tx_id = f"tx-{str(uuid.uuid4())[:8]}"
    
    if req.command == "RDO":
        v_reg["door_locked"] = 0 # 문 열림
        print("🔓 [MT 제어] RDO (Remote Door Open) 명령 수신 ➔ 가상 도어 액추에이터 구동 완료.")
        return {"status": "RDO_SUCCESS", "door_locked": v_reg["door_locked"]}
        
    elif req.command == "RSC":
        print("📋 [MT 제어] RSC (Remote Status Check) 명령 수신 ➔ 현재 차량 레지스터 취합 중.")
        return {"status": "RSC_SUCCESS", "current_vehicle_reg": v_reg}

@app.get("/hardware/status")
async def get_hardware_status():
    return v_reg

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8050)