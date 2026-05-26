import os
import time
from fastapi import FastAPI, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
import uvicorn
from app.api import control

app = FastAPI(
    title="TMS (Telematics Monitoring System) Server",
    description="QA Automation Portfolio - Telematics Monitoring & Verification Server",
    version="2.0.0"
)

# -----------------------------------------------------------------------------
# [In-Memory 데이터베이스 모사]
# -----------------------------------------------------------------------------
# 1. 단말기 개통 데이터베이스 (Provisioning Registry)
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

# 2. TMS 트랜잭션 히스토리 저장소 (최근 50개 보관)
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
    """TMS 대시보드에서 비동기 폴링으로 가져갈 트랜잭션 목록 조회"""
    return JSONResponse(content=LMS_TRANSACTIONS[::-1][:30])  # 최근 등록 순 정렬

@app.post("/api/v1/telematics/status")
async def update_telematics_status(update: TelematicsStatusUpdate):
    """
    차량 CCU가 제어를 완료한 후 무선 복귀 신호(MO)를 보내는 포트
    """
    for tx in LMS_TRANSACTIONS:
        if tx["transaction_id"] == update.transaction_id:
            tx["status"] = "COMPLETED" if update.status_code in [2000, 2010, 2040, 2041] else "FAILED"
            tx["status_code"] = update.status_code
            
            # [실무 가양 반영] 복귀 채널 통과 시 차량 상태 레지스터(ECU) 천이 모델 업데이트
            if update.status_code == 2000:
                tx["vehicle_state"]["climate_state"]["active"] = 1
                tx["vehicle_state"]["engine_status"] = 1 # 공조 구동 시 시동 ON
            elif update.status_code == 2010:
                tx["vehicle_state"]["engine_status"] = 1
            elif update.status_code == 2040:
                tx["vehicle_state"]["rdo_state"]["lock"] = 1
            elif update.status_code == 2041:
                tx["vehicle_state"]["rdo_state"]["lock"] = 0
                
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
@app.get("/lms", response_class=HTMLResponse) # 이전 경로 하위 호환성 유지
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
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)