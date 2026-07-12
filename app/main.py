import os
import time
import uuid
import copy
from typing import Dict, Any, Optional
from fastapi import FastAPI, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
import uvicorn
from app.api import control

app = FastAPI(
    title="TMS (Telematics Monitoring System) Server",
    description="QA Automation Portfolio - Wire Power Transitions & Unified R** Commands",
    version="2.2.0"
)

# -----------------------------------------------------------------------------
# [In-Memory 데이터베이스 모사]
# -----------------------------------------------------------------------------
VEHICLE_REGISTRY = {
    "KMHCT41BPJU123456": {
        "nad_id": "NAD_SKT_2026_9999",
        "carrier": "SKT",
        "eco_type": "EV",
        "is_activated": True,
        "model_name": "IONIQ 6"
    },
    "KMHCT41BPJU888888": {
        "nad_id": "NAD_KT_2026_1111",
        "carrier": "KT",
        "eco_type": "ICE",
        "is_activated": False,  # 테스트용 미개통 단말
        "model_name": "GENESIS G80"
    }
}

# 🌟 [신설] 차량 독립 상태 관리 데이터베이스 (Vehicle Status Service - VSS DB)
# 원격 제어 트랜잭션과 무관하게, 각 차대번호(VIN) 차량의 실제 물리/논리 상태를 독자적으로 보존합니다.
VEHICLE_STATES = {
    "KMHCT41BPJU123456": {
        "power_state": "SLEEP",
        "panel_acc": 0,
        "wire_acc": 0,
        "ign1": 0,
        "ign3": 0,
        "sleepmode": 1,
        "door_locked": 1,
        "room_temp": 22.5,
        "boot_reason": "00",
        "vssc_version": "VSSC-IONIQ6-REV-9872"
    },
    "KMHCT41BPJU888888": {
        "power_state": "SLEEP",
        "panel_acc": 0,
        "wire_acc": 0,
        "ign1": 0,
        "ign3": 0,
        "sleepmode": 1,
        "door_locked": 1,
        "room_temp": 22.0,
        "boot_reason": "00",
        "vssc_version": "VSSC-G80-REV-1102"
    }
}

# TMS 트랜잭션 히스토리 저장소 (최근 수집된 세션 및 MO 로그 포함)
LMS_TRANSACTIONS = []

# CCU 단말 규격과 1:1 싱크를 맞춘 인바운드 텔레매틱스 DTO 정의
class TelematicsStatusUpdate(BaseModel):
    transaction_id: str
    vin: str
    msg_type: str                  # VSS-C, VSS-A-WAKEUP, VSS-A-ALLDATA-BACKUP 등
    message: str                   # 런타임 텍스트 로그 및 원인 코드
    current_reg: Dict[str, Any]    # 차량 CCU 내부 런타임 레지스터 현황

# -----------------------------------------------------------------------------
# [TMS 및 API 라우터 등록]
# -----------------------------------------------------------------------------
app.include_router(control.router, prefix="/api/v1")

@app.get("/")
async def root():
    return {"status": "ok", "message": "TMS Telematics Server is running!"}

# -----------------------------------------------------------------------------
# [TMS 관측 전용 엔드포인트]
# -----------------------------------------------------------------------------
@app.get("/api/v1/lms/transactions")
async def get_lms_transactions():
    """TMS 대시보드 비동기 폴링 수집을 위해 최근 트랜잭션 30개 역순 정렬 반환"""
    return JSONResponse(content=LMS_TRANSACTIONS[::-1][:30])

# 🌟 [신설] 특정 차종의 현재 순수 VSS 물리적 상태 조회 엔드포인트
@app.get("/api/v1/telematics/vss/{vin}")
async def get_vehicle_vss_state(vin: str):
    if vin not in VEHICLE_STATES:
        return JSONResponse(status_code=404, content={"message": "Vehicle not found"})
    return VEHICLE_STATES[vin]

@app.post("/api/v1/telematics/status")
async def update_telematics_status(update: TelematicsStatusUpdate):
    """
    차량 CCU가 주도적으로 송출하는 다양한 MO(Mobile Originated) 통보 및 
    VSS 동기화 패킷을 통합 수신하여 독립 VSS DB를 갱신하고,
    각 전송 이벤트를 좌측 대시보드에 독립적인 VSS 카드 로그로 쌓아 올립니다.
    """
    current_time = time.time()
    reg = update.current_reg
    
    # 1. 🌟 차량 독립 VSS 데이터베이스 실시간 동기화 업데이트 (Decoupling)
    if update.vin in VEHICLE_STATES:
        VEHICLE_STATES[update.vin].update({
            "power_state": reg.get("power_state", "SLEEP"),
            "panel_acc": reg.get("panel_acc", 0),
            "wire_acc": reg.get("wire_acc", 0),
            "ign1": reg.get("ign1", 0),
            "ign3": reg.get("ign3", 0),
            "sleepmode": reg.get("sleepmode", 1),
            "door_locked": reg.get("door_locked", 1),
            "room_temp": reg.get("room_temp", 22.0),
            "boot_reason": reg.get("boot_reason", "00"),
            "vssc_version": reg.get("vssc_version", "VSSC-UNKNOWN")
        })
    
    # 동기화된 데이터 추출
    v_state = VEHICLE_STATES.get(update.vin, reg)
    wire_acc = v_state.get("wire_acc", 0)
    wire_ign1 = v_state.get("ign1", 0)
    panel_acc = v_state.get("panel_acc", 0)

    # 가상 차량 프로필 조회
    profile = VEHICLE_REGISTRY.get(update.vin, {
        "nad_id": "UNREGISTERED",
        "carrier": "UNKNOWN",
        "eco_type": "EV",
        "is_activated": True,
        "model_name": "IONIQ 6"
    })

    # [1] 기존 발급된 제어 트랜잭션(App 기동 원격제어)을 찾아 다이어그램 흐름 업데이트
    tx = next((t for t in LMS_TRANSACTIONS if t["transaction_id"] == update.transaction_id), None)
    
    if tx:
        # 분리된 VSS 실시간 상태를 부모 제어 트랜잭션에 투영
        tx["vehicle_state"]["wires"]["acc"] = wire_acc
        tx["vehicle_state"]["wires"]["ign1"] = wire_ign1
        tx["vehicle_state"]["panel_acc"] = panel_acc
        tx["vehicle_state"]["rdo_state"]["lock"] = v_state.get("door_locked", 1)
        
        # 논리 전원 레지스터 상태 결정
        if v_state.get("power_state") == "READY" and v_state.get("sleepmode") == 0:
            tx["vehicle_state"]["ign_state"] = 3  # KEY_START 시동 인지
        elif wire_ign1 == 1:
            tx["vehicle_state"]["ign_state"] = 2  # IGN_ON
        elif panel_acc == 1 or wire_acc == 1:
            tx["vehicle_state"]["ign_state"] = 1  # ACC (물리 혹은 패널 인입)
        else:
            tx["vehicle_state"]["ign_state"] = 0  # PANEL_OFF

        if v_state.get("power_state") == "READY" and v_state.get("sleepmode") == 0:
            tx["vehicle_state"]["climate_state"]["active"] = 1
            tx["vehicle_state"]["climate_state"]["target_temp"] = v_state.get("room_temp", 22.0)
        else:
            tx["vehicle_state"]["climate_state"]["active"] = 0

        tx["vehicle_feedback"] = f"[{update.msg_type}] {update.message}"

        # 부모 트랜잭션 다이어그램 스텝 빌드업
        if update.msg_type == "VSS-C":
            tx["steps"].append({
                "from": "CCU", "to": "Server", "msg": "VSS_SYNC (Code 204: VSS-C Version)", "timestamp": current_time
            })
        elif update.msg_type == "VSS-A-WAKEUP":
            tx["steps"].append({
                "from": "CCU", "to": "Server", "msg": f"VSS_SYNC (Code 204: VSS-A Wakeup | BootReason:{v_state.get('boot_reason','00')})", "timestamp": current_time
            })
        elif update.msg_type == "VSS-A-ALLDATA-BACKUP":
            tx["steps"].append({
                "from": "CCU", "to": "Server", "msg": "VSS_SYNC (Code 204: All-Data Backup)", "timestamp": current_time
            })
        else:
            # 최종 제어 완료 통보 처리 (Code 200 MRC_SUCCESS)
            tx["status"] = "COMPLETED"
            tx["status_code"] = 200
            tx["steps"].append({
                "from": "CCU", "to": "Server", "msg": f"STATUS_REPORT (Code 200: {update.message})", "timestamp": current_time
            })
            tx["steps"].append({
                "from": "Server", "to": "App", "msg": "PUSH_NOTIFICATION", "timestamp": current_time
            })

    # [2] 🌟 VSS 및 Wakeup 개별 데이터를 독립적인 "VSS 전용 트랜잭션 카드"로 생성하여 좌측 목록에 주르륵 누적 적립
    event_tx_id = f"evt-{str(uuid.uuid4())[:8]}"
    
    ign_state_mapped = 0
    if v_state.get("power_state") == "READY" and v_state.get("sleepmode") == 0:
        ign_state_mapped = 3
    elif wire_ign1 == 1:
        ign_state_mapped = 2
    elif panel_acc == 1 or wire_acc == 1:
        ign_state_mapped = 1

    # 독립적인 VSS 이벤트 카드 구조화
    event_card = {
        "transaction_id": event_tx_id,
        "vin": update.vin,
        "service": "VSS",           # 명확히 VSS 서비스 채널로 별도 분리
        "command": update.msg_type, # VSS-C, VSS-A-WAKEUP, VSS-A-ALLDATA-BACKUP 등으로 표시되어 쌓임
        "status": "COMPLETED",
        "status_code": 204,
        "provision_state": {
            "vin": update.vin,
            "nad_id": profile["nad_id"],
            "carrier": profile["carrier"],
            "eco_type": profile["eco_type"],
            "is_activated": profile["is_activated"]
        },
        "vehicle_state": {
            "ign_state": ign_state_mapped,
            "wires": {"b1": 1, "acc": wire_acc, "ign1": wire_ign1},
            "panel_acc": panel_acc,
            "rdo_state": {"lock": v_state.get("door_locked", 1), "door_open": 0},
            "climate_state": {
                "active": 1 if ign_state_mapped == 3 else 0,
                "target_temp": v_state.get("room_temp", 22.0)
            }
        },
        "profile": profile,
        "vehicle_feedback": f"[{update.msg_type}] {update.message}",
        "steps": [
            {
                "from": "CCU", 
                "to": "Server", 
                "msg": f"VSS_SYNC_DETECTED ({update.msg_type})", 
                "timestamp": current_time
            }
        ]
    }
    LMS_TRANSACTIONS.append(event_card)

    return {"status": "ACK"}

# -----------------------------------------------------------------------------
# [TMS 실시간 모니터링 대시보드 서빙]
# -----------------------------------------------------------------------------
@app.get("/tms", response_class=HTMLResponse)
@app.get("/lms", response_class=HTMLResponse)
async def get_tms_dashboard():
    """
    app/templates/tms.html 소스 파일을 정적 로드하여 관제 시스템 대시보드를 서빙합니다.
    """
    template_path = os.path.join("app", "templates", "tms.html")
    
    if os.path.exists(template_path):
        try:
            with open(template_path, "r", encoding="utf-8") as f:
                html_content = f.read()
            return HTMLResponse(content=html_content)
        except Exception as e:
            print(f"❌ [TMS 대시보드] 템플릿 로드 실패: {str(e)}")
            
    backup_html = f"""
    <html>
        <body style="background:#090d16; color:#f8fafc; font-family:monospace; text-align:center; padding-top:100px;">
            <h1>⚠️ TMS 템플릿 로드 실패</h1>
            <p><strong>{template_path}</strong> 파일의 위치를 점검해 주십시오.</p>
        </body>
    </html>
    """
    return HTMLResponse(content=backup_html, status_code=500)

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)