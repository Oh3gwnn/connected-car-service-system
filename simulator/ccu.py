import time
import uuid
from fastapi import FastAPI, BackgroundTasks
from pydantic import BaseModel
import requests
import uvicorn

app = FastAPI(title="Automotive CCU (HILs Core Spec)")

# [실차 런타임 레지스터 사양]
v_reg = {
    "vin": "KMHCT41BPJU123456",
    "power_state": "SLEEP",
    "panel_acc": 0,
    "wire_acc": 0,
    "ign1": 0,
    "ign3": 0,
    "sleepmode": 1,           # 1: SLEEP, 0: WAKEUP
    "door_locked": 1,         # 1: LOCKED, 0: UNLOCKED (Door 상태)
    "room_temp": 22.5,        # 차량 내부 온도 상태
    "boot_reason": "00",      # 00: 초기화, 01: Normal Boot, 02: SVN B-Alarm
    "vssc_version": "VSSC-IONIQ6-REV-9872" # 직전 상태 이력 일련번호
}

TMS_SERVER_URL = "http://127.0.0.1:8000/api/v1/telematics/status"

class SignalPayload(BaseModel):
    signal: str

def execute_wakeup_sequence(tx_id: str):
    """[TC-PWR-WAKEUP] 5초 부팅 지연 후 MQTT 연결 및 VSS-A 통보 시퀀스"""
    global v_reg
    print(f"⏳ [BOOT] 가상 시스템 커널 부팅 개시... (Boot Reason: {v_reg['boot_reason']})")
    time.sleep(5.0) # 5초 부팅 레이턴시 재현
    
    v_reg["power_state"] = "READY"
    v_reg["sleepmode"] = 0 # WAKEUP 상태 천이
    print("🌐 [NETWORK] 이더넷 링크 체결 성공. MQTT Connected & Token 발급 완료.")
    
    # [MO 통보] VSS-C (직전 상태 이력 일련번호) 전송
    send_to_server(tx_id, "VSS-C", f"VERSION_INFO: {v_reg['vssc_version']}")
    
    # [MO 통보] VSS-A (Wakeup 상태 통보) -> sleepmode:0, acc:1, ign1/3:0(디폴트유지)
    vss_a_msg = f"acc=1, sleepmode=0, ign1={v_reg['ign1']}, ign3={v_reg['ign3']}, boot_reason={v_reg['boot_reason']}"
    send_to_server(tx_id, "VSS-A-WAKEUP", vss_a_msg)

def execute_sleep_sequence(tx_id: str):
    """[TC-PWR-SLEEP] 10초 유예기간 동안 모든 정보(All Data)를 응집하여 백업 및 최종 Version 전송 후 슬립"""
    global v_reg
    print("⏳ [SLEEP] ACC OFF 감지. 10초 슬립 유예기간(Grace Period) 진입...")
    
    # 10초 동안 가상 CAN 버스 유지 시간 모사 (시간 조건)
    time.sleep(10.0)
    
    # [MO 통보] 슬립 직전 차량의 모든 정보(All Data)를 묶어서 전송 + 최종 Version 명시
    all_data_payload = (
        f"STATUS=SLEEP, acc=0, sleepmode=1, door_state={'LOCKED' if v_reg['door_locked']==1 else 'OPEN'}, "
        f"temp={v_reg['room_temp']}V, FINAL_VERSION_SERIAL={v_reg['vssc_version']}"
    )
    send_to_server(tx_id, "VSS-A-ALLDATA-BACKUP", all_data_payload)
    
    # 최종 물리 전원 슬립 및 통신 차단
    v_reg["power_state"] = "SLEEP"
    v_reg["sleepmode"] = 1
    v_reg["boot_reason"] = "00"
    print("📡 [MQTT] CCU ➔ 서버 무선 소켓 세션 차단 완료 (MQTT DISCONNECTED).")
    print("⚡ [SLEEP] CAN Bus CRC 송출 정지. 전압 0V 수렴 완료.\n")

def send_to_server(tx_id: str, msg_type: str, message: str):
    """기존 main.py 관제 서버 허브로 규격 신호 릴레이 전송"""
    try:
        payload = {
            "transaction_id": tx_id,
            "vin": v_reg["vin"],
            "msg_type": msg_type,
            "message": message,
            "current_reg": v_reg.copy()
        }
        requests.post(TMS_SERVER_URL, json=payload, timeout=2)
        print(f"📡 [차량 ➔ 서버] 송신 성공 | 타입: {msg_type} | 데이터: {message}")
    except Exception:
        print(f"⚠️ [차량 ➔ 서버] 통신 실패 (서버가 켜져 있는지 확인하세요)")

# --- 하드웨어 물리 조작 스위치 인터페이스 ---
@app.post("/hardware/signal")
async def receive_hardware_signal(req: SignalPayload, bg_tasks: BackgroundTasks):
    global v_reg
    tx_id = f"tx-{str(uuid.uuid4())[:8]}"
    
    if req.signal == "ACC_ON":
        # 사전 조건이 SLEEP 상태일 때 정상 시동 기동
        v_reg["panel_acc"] = 1
        v_reg["wire_acc"] = 1
        v_reg["boot_reason"] = "01" # Normal Boot 비트 코드 세팅
        v_reg["power_state"] = "BOOTING"
        
        bg_tasks.add_task(execute_wakeup_sequence, tx_id)
        return {"status": "WAKEUP_STARTED", "transaction_id": tx_id}
        
    elif req.signal == "ACC_OFF":
        v_reg["panel_acc"] = 0
        v_reg["wire_acc"] = 0
        v_reg["power_state"] = "SHUTTING_DOWN"
        
        bg_tasks.add_task(execute_sleep_sequence, tx_id)
        return {"status": "SLEEP_STARTED", "transaction_id": tx_id}

    elif req.signal == "BPLUS_RESET":
        v_reg = {"vin": "KMHCT41BPJU123456", "power_state": "SLEEP", "panel_acc": 0, "wire_acc": 0, "ign1": 0, "ign3": 0, "sleepmode": 1, "door_locked": 1, "room_temp": 22.5, "boot_reason": "00", "vssc_version": "VSSC-IONIQ6-REV-9872"}
        print("⚡ [B+ RESET] 배터리 상시 전원 완전 리셋 완료.")
        return {"status": "RESET_COMPLETED"}

@app.get("/hardware/status")
async def get_hardware_status():
    return v_reg

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8050)