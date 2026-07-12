import time
import uuid
import random
import threading
from fastapi import FastAPI, BackgroundTasks, HTTPException
from pydantic import BaseModel
import requests
import uvicorn

app = FastAPI(title="Enhanced Automotive CCU Daemon (HILs)")

# VSS-C: 직전 슬립 진입 시 백업된 상태 정보의 일련번호 데이터베이스
VSSC_VERSION_DATABASE = {
    "KMHCT41BPJU123456": "VSSC-IONIQ6-REV-9872",
    "KMHCT41BPJU888888": "VSSC-G80-REV-1102"
}

# 런타임 하드웨어 전원 및 통신 레지스터 (초기 상태: SLEEP)
v_reg = {
    "vin": "KMHCT41BPJU123456",
    "power_state": "SLEEP",        # SLEEP, BOOTING, READY
    "panel_acc": 0,               # 0: OFF, 1: ON (내부 로깅 전용)
    "wire_acc": 0,                # 0: OFF, 1: ON (물리 UI 매핑 전용)
    "ign1": 0,                    # 0: OFF, 1: ON (디폴트 유지 대상)
    "ign3": 0,                    # 0: OFF, 1: ON (디폴트 유지 대상)
    "sleepmode": 1,               # 1: SLEEP, 0: WAKEUP
    "is_ethernet_up": False,
    "current_vssc_version": "VSSC-UNKNOWN"
}

TMS_SERVER_URL = "http://127.0.0.1:8000/api/v1/telematics/status"

class SignalPayload(BaseModel):
    signal: str  # BPLUS_RESET, WAKEUP_TRIGGER

def execute_wakeup_sequence(vin: str, tx_id: str):
    global v_reg
    
    # 1. 5초간의 물리 커널 부팅 지연 모사
    print(f"⏳ [BOOT] 가상 시스템 커널 부팅 시작 (5초 지연 적용)...")
    time.sleep(5.0)
    
    # 2. 이더넷 링크 체결 및 무선망 수립 (토큰 발급 완료)
    print(f"🌐 [NETWORK] DCU-CCU 고속 이더넷 PHY 링크업 성공. MQTT 토큰 정상 발급.")
    v_reg["is_ethernet_up"] = True
    v_reg["power_state"] = "READY"
    v_reg["sleepmode"] = 0  # WAKEUP 상태 천이
    
    # 3. VSS-C 직전 슬립 이력 버전(Serial Info) 매칭 복원
    v_reg["current_vssc_version"] = VSSC_VERSION_DATABASE.get(vin, "VSSC-DEFAULT-0000")
    print(f"🔑 [VSS-C] 직전 슬립 이력 버전 일련번호 복원 성공: [{v_reg['current_vssc_version']}]")
    
    # 4. 관제 서버로 VSS 변동 데이터 동기화 보고 (상태코드 204 발송)
    vss_message = f"[VSS SYNC] sleepmode=0(WAKEUP), acc=1, ign1={v_reg['ign1']}, ign3={v_reg['ign3']} | VSS-C Serial={v_reg['current_vssc_version']}"
    report_to_tms(tx_id, vin, 204, vss_message)
    
    # 5. 최종 제어 완료 보고 (상태코드 200 발송)
    time.sleep(1.0)
    report_to_tms(tx_id, vin, 200, f"RSC_WAKEUP_SUCCESS: {vin} 무선 복귀 성공")

def report_to_tms(tx_id: str, vin: str, status_code: int, message: str):
    try:
        payload = {
            "transaction_id": tx_id,
            "vin": vin,
            "status_code": status_code,
            "message": message
        }
        res = requests.post(TMS_SERVER_URL, json=payload, timeout=3)
        if res.status_code == 200:
            print(f"📡 [CCU -> Server] 통보 성공 | Code {status_code} | Msg: {message}")
    except Exception as e:
        print(f"❌ [CCU -> Server] 네트워크 예외 발생: {str(e)}")

@app.post("/hardware/signal")
async def receive_hardware_signal(req: SignalPayload, bg_tasks: BackgroundTasks):
    global v_reg
    
    if req.signal == "BPLUS_RESET":
        v_reg = {
            "vin": "KMHCT41BPJU123456",
            "power_state": "SLEEP",
            "panel_acc": 0,
            "wire_acc": 0,
            "ign1": 0,
            "ign3": 0,
            "sleepmode": 1,
            "is_ethernet_up": False,
            "current_vssc_version": "VSSC-UNKNOWN"
        }
        print("⚡ [B+ RESET] 모든 휘발성 전장 레지스터 디폴트 원복 완료.")
        return {"status": "RESET_COMPLETED", "detail": v_reg}
        
    elif req.signal == "WAKEUP_TRIGGER":
        v_reg["panel_acc"] = 1
        v_reg["wire_acc"] = 1
        v_reg["power_state"] = "BOOTING"
        
        tx_id = f"tx-{str(uuid.uuid4())[:8]}"
        print(f"📝 [LOG] Panel ACC = ON 감지 (내부 로깅)")
        print(f"🔌 [WIRE] Wire ACC = ON 인가 완료 (물리 UI 연동)")
        
        bg_tasks.add_task(execute_wakeup_sequence, v_reg["vin"], tx_id)
        return {"status": "TRANSITION_STARTED", "transaction_id": tx_id}

@app.get("/hardware/status")
async def get_hardware_status():
    return v_reg

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8050)