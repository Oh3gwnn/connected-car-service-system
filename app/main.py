import os
import time
import uuid
import copy
from fastapi import FastAPI, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
import uvicorn
from app.api import control

app = FastAPI(
    title="TMS (Telematics Monitoring System) Server",
    description="QA Automation Portfolio - Wire Power Transitions & Unified R** Commands",
    version="2.1.1"
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

LMS_TRANSACTIONS = []

class TelematicsStatusUpdate(BaseModel):
    transaction_id: str
    vin: str
    status_code: int
    message: str

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
    return JSONResponse(content=LMS_TRANSACTIONS[::-1][:30])

@app.post("/api/v1/telematics/status")
async def update_telematics_status(update: TelematicsStatusUpdate):
    """
    차량 CCU가 제어를 완료한 후 무선 복귀 신호(MO) 및 VSS를 보고하는 포트
    """
    # 🌟 [피드백 완벽 반영] VSS 동기화 패킷(204) 처리 루틴
    if update.status_code == 204:
        for tx in LMS_TRANSACTIONS:
            if tx["transaction_id"] == update.transaction_id:
                # 1. 제어 트랜잭션(tx)의 실제 차량 상태 레지스터만 실시간으로 업데이트 수행
                if "RDO_LOCK" in tx["command"]:
                    tx["vehicle_state"]["rdo_state"]["lock"] = 1
                elif "RDO_UNLOCK" in tx["command"]:
                    tx["vehicle_state"]["rdo_state"]["lock"] = 0
                elif "CLIMATE" in tx["command"] or "ENGINE" in tx["command"]:
                    tx["vehicle_state"]["ign_state"] = 3
                    tx["vehicle_state"]["wires"]["acc"] = 1
                    tx["vehicle_state"]["wires"]["ign1"] = 1
                    if "CLIMATE" in tx["command"]:
                        tx["vehicle_state"]["climate_state"]["active"] = 1

                # 🌟 [버그 수정]: 부모 MRC 트랜잭션의 steps에는 204 화살표를 절대 추가하지 않습니다! (완전 격리)

                # 2. 오직 VSS 독립 관제 카드에서만 관측되도록 별개의 독자적인 VSS 트랜잭션을 생성 후 등록
                vss_tx = {
                    "transaction_id": f"vss-{str(uuid.uuid4())[:8]}", # VSS 전용 세션 식별키
                    "vin": update.vin,
                    "service": "VSS",
                    "command": "VSS_STATUS_SYNC",
                    "status": "COMPLETED",
                    "status_code": 204,
                    "provision_state": tx["provision_state"],
                    "vehicle_state": copy.deepcopy(tx["vehicle_state"]),
                    "profile": tx["profile"],
                    "vehicle_feedback": update.message,
                    "steps": [
                        {
                            "from": "CCU", 
                            "to": "Server", 
                            "msg": "VSS_SYNC (Code 204)", 
                            "timestamp": time.time()
                        }
                    ]
                }
                LMS_TRANSACTIONS.append(vss_tx)
                return {"status": "ACK"}

    # 3. MRC 원격 제어 성공 패킷(200) 처리 루틴 (MT-MO 완성)
    for tx in LMS_TRANSACTIONS:
        if tx["transaction_id"] == update.transaction_id:
            if update.status_code == 200:
                tx["status"] = "COMPLETED"
                tx["status_code"] = 200
                
                # 중복 제어로 인해 VSS(204) 단계를 거치지 않고 다이렉트로 200 성공 보고가 온 경우의 백업 처리
                if "RDO_LOCK" in tx["command"]:
                    tx["vehicle_state"]["rdo_state"]["lock"] = 1
                elif "RDO_UNLOCK" in tx["command"]:
                    tx["vehicle_state"]["rdo_state"]["lock"] = 0
                elif "CLIMATE" in tx["command"] or "ENGINE" in tx["command"]:
                    tx["vehicle_state"]["ign_state"] = 3
                    tx["vehicle_state"]["wires"]["acc"] = 1
                    tx["vehicle_state"]["wires"]["ign1"] = 1
                    if "CLIMATE" in tx["command"]:
                        tx["vehicle_state"]["climate_state"]["active"] = 1

                # 🌟 원격 제어 성공 다이어그램에는 VSS_SYNC선 없이 순수 MT-MO 단계만 추가됩니다.
                tx["steps"].append({
                    "from": "CCU", "to": "Server", "msg": "STATUS_REPORT (Code 200)", "timestamp": time.time()
                })
                tx["steps"].append({
                    "from": "Server", "to": "App", "msg": "PUSH_NOTIFICATION", "timestamp": time.time()
                })
                tx["vehicle_feedback"] = update.message
                
            else:
                # 실패 처리 루틴
                tx["status"] = "FAILED"
                tx["status_code"] = update.status_code
                tx["vehicle_state"]["ign_state"] = 0
                tx["vehicle_state"]["wires"]["acc"] = 0
                tx["vehicle_state"]["wires"]["ign1"] = 0
                tx["vehicle_state"]["climate_state"]["active"] = 0
                
                tx["steps"].append({
                    "from": "CCU", "to": "Server", "msg": f"STATUS_REPORT (Code {update.status_code})", "timestamp": time.time()
                })
                tx["steps"].append({
                    "from": "Server", "to": "App", "msg": "PUSH_NOTIFICATION", "timestamp": time.time()
                })
                tx["vehicle_feedback"] = update.message
                
            return {"status": "ACK"}
            
    return JSONResponse(status_code=404, content={"message": "Transaction not found"})

# -----------------------------------------------------------------------------
# [TMS 실시간 모니터링 대시보드 (HTML 외부 템플릿 연동)]
# -----------------------------------------------------------------------------
@app.get("/tms", response_class=HTMLResponse)
@app.get("/lms", response_class=HTMLResponse)
async def get_tms_dashboard():
    template_path = os.path.join("app", "templates", "tms.html")
    
    if os.path.exists(template_path):
        try:
            with open(template_path, "r", encoding="utf-8") as f:
                html_content = f.read()
            return HTMLResponse(content=html_content)
        except Exception as e:
            print(f"❌ [TMS 대시보드] 파일 로드 실패: {str(e)}")
            
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