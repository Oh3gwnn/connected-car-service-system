from fastapi import FastAPI, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import uvicorn
import time
from app.api import control

app = FastAPI(
    title="CCS (Car Connected Service) Simulator Server",
    description="QA Automation Portfolio - Kafka & MQTT Integrated Backend with LMS View",
    version="1.0.0"
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
        "is_activated": False, # 테스트용 미개통 단말
        "model_name": "GENESIS G80"
    }
}

# 2. LMS 트랜잭션 히스토리 저장소 (최근 50개 보관)
LMS_TRANSACTIONS = []

class TelematicsStatusUpdate(BaseModel):
    transaction_id: str
    vin: str
    status_code: int
    message: str

# -----------------------------------------------------------------------------
# [LMS 및 API 라우터 등록]
# -----------------------------------------------------------------------------
app.include_router(control.router, prefix="/api/v1")

@app.get("/")
async def root():
    return {"status": "ok", "message": "CCS Telematics Server is running!"}

# -----------------------------------------------------------------------------
# [LMS 관측 전용 엔드포인트]
# -----------------------------------------------------------------------------
@app.get("/api/v1/lms/transactions")
async def get_lms_transactions():
    """LMS 대시보드에서 비동기 폴링으로 가져갈 트랜잭션 목록 조회"""
    return JSONResponse(content=LMS_TRANSACTIONS[::-1][:30])  # 최근 등록 순 정렬

@app.post("/api/v1/telematics/status")
async def update_telematics_status(update: TelematicsStatusUpdate):
    """
    차량 CCU가 제어를 완료한 후 무선 복귀 신호(MO)를 보내는 포트
    """
    for tx in LMS_TRANSACTIONS:
        if tx["transaction_id"] == update.transaction_id:
            tx["status"] = "COMPLETED" if update.status_code in [2000, 2040] else "FAILED"
            tx["status_code"] = update.status_code
            tx["steps"].append({
                "from": "CCU", "to": "Server", "msg": f"STATUS_REPORT (Code {update.status_code})", "timestamp": time.time()
            })
            tx["steps"].append({
                "from": "Server", "to": "App", "msg": f"PUSH_NOTIFICATION ({update.message})", "timestamp": time.time()
            })
            tx["vehicle_feedback"] = update.message
            return {"status": "ACK"}
    return JSONResponse(status_code=404, content={"message": "Transaction not found"})

# -----------------------------------------------------------------------------
# [LMS 실시간 모니터링 대시보드 (HTML 페이지 내장)]
# -----------------------------------------------------------------------------
@app.get("/lms", response_class=HTMLResponse)
async def get_lms_dashboard():
    """실시간으로 모바일 알림 팝업 및 사다리 로그 시퀀스를 관찰하는 통합 LMS 대시보드"""
    html_content = """
    <!DOCTYPE html>
    <html lang="ko">
    <head>
        <meta charset="UTF-8">
        <title>LMS Real-Time Vehicle Sequence Monitor</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css" rel="stylesheet">
    </head>
    <body class="bg-slate-900 text-slate-100 font-sans h-screen flex flex-col overflow-hidden">
        <!-- 상단 헤더 -->
        <header class="bg-slate-800 border-b border-slate-700 px-6 py-4 flex justify-between items-center shadow-lg">
            <div class="flex items-center space-x-3">
                <i class="fa-solid fa-diagram-project text-teal-400 text-2xl"></i>
                <h1 class="text-xl font-bold tracking-wider">LMS CCS Real-Time Ladder Monitor</h1>
            </div>
            <div class="flex space-x-4 items-center">
                <span class="bg-teal-500/10 text-teal-400 border border-teal-500/20 px-3 py-1 rounded text-xs font-semibold animate-pulse">
                    <i class="fa-solid fa-wifi mr-1"></i> 통신 활성화 상태
                </span>
                <span class="text-slate-400 text-xs">포트폴리오 검증용 대시보드</span>
            </div>
        </header>

        <!-- 메인 콘텐츠 레이아웃 -->
        <main class="flex-1 flex overflow-hidden">
            <!-- 좌측: 트랜잭션 목록 히스토리 -->
            <section class="w-1/3 border-r border-slate-700 p-4 flex flex-col overflow-hidden bg-slate-900/50">
                <h2 class="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-3">
                    <i class="fa-solid fa-list mr-1"></i> 제어 트랜잭션 이력 (RSC / MRC-A)
                </h2>
                <div id="tx-list" class="flex-1 overflow-y-auto space-y-2 pr-1">
                    <!-- 동적 리스트가 채워지는 구역 -->
                    <div class="text-center text-slate-500 py-10">요청 대기 중...</div>
                </div>
            </section>

            <!-- 중앙: 사다리 그래프 시퀀스 다이어그램 -->
            <section class="flex-1 p-6 flex flex-col overflow-hidden bg-slate-950/40 justify-between">
                <div class="flex-1 overflow-y-auto" id="ladder-container">
                    <h2 class="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-6">
                        <i class="fa-solid fa-share-nodes mr-1"></i> E2E 통신 사다리 그래프 (Sequence Log)
                    </h2>
                    
                    <div id="ladder-diagram" class="hidden flex-col items-center justify-start space-y-4 py-4 min-h-[400px]">
                        <!-- 물리적 통신 4대 기둥 -->
                        <div class="flex justify-between w-full max-w-2xl px-12 border-b border-slate-800 pb-2 mb-6">
                            <span class="font-bold text-sky-400"><i class="fa-solid fa-mobile-screen mr-1"></i> APP</span>
                            <span class="font-bold text-indigo-400"><i class="fa-solid fa-server mr-1"></i> Server</span>
                            <span class="font-bold text-yellow-400"><i class="fa-solid fa-tower-broadcast mr-1"></i> CCU</span>
                            <span class="font-bold text-emerald-400"><i class="fa-solid fa-microchip mr-1"></i> ECU</span>
                        </div>
                        
                        <!-- 화살표 시퀀스들이 노출될 콘텐트 -->
                        <div id="sequence-steps" class="w-full max-w-2xl relative space-y-6"></div>
                    </div>
                    
                    <div id="empty-ladder" class="flex flex-col items-center justify-center h-full text-slate-500 space-y-3">
                        <i class="fa-solid fa-diagram-successor text-5xl text-slate-700 animate-bounce"></i>
                        <p>좌측 트랜잭션 목록에서 분석할 요청 로그를 클릭하세요.</p>
                    </div>
                </div>
                
                <!-- 하단 디코딩 결과 창 -->
                <div class="h-1/3 border-t border-slate-800 p-4 bg-slate-900/80 rounded-t-lg flex flex-col justify-between">
                    <h3 class="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-2">
                        <i class="fa-solid fa-terminal mr-1"></i> 가상 단말(ECU) 최종 변동 감지 및 응답 데이터
                    </h3>
                    <div class="grid grid-cols-2 gap-4 flex-1">
                        <div class="bg-slate-950 p-3 rounded border border-slate-800 overflow-y-auto text-xs font-mono" id="tx-details">
                            선택된 트랜잭션 상세 JSON 패킷이 표시됩니다.
                        </div>
                        <div class="bg-slate-950 p-3 rounded border border-slate-800 flex flex-col justify-between text-xs font-mono">
                            <div><span class="text-slate-500">차종 ECO Type:</span> <span id="car-eco" class="text-slate-300">-</span></div>
                            <div><span class="text-slate-500">CCU 단말 ID:</span> <span id="car-nad" class="text-slate-300">-</span></div>
                            <div><span class="text-slate-500">차량 동작 상태:</span> <span id="car-action" class="text-slate-300">-</span></div>
                            <div><span class="text-slate-500">E2E 통신 결과:</span> <span id="car-result" class="text-slate-300">-</span></div>
                        </div>
                    </div>
                </div>
            </section>

            <!-- 우측: 모바일 앱 디바이스 시뮬레이터 (푸시 알림) -->
            <section class="w-80 border-l border-slate-700 p-6 flex flex-col justify-center items-center bg-slate-900/70">
                <div class="w-64 h-[500px] bg-slate-950 border-4 border-slate-700 rounded-[32px] p-3 flex flex-col justify-between relative shadow-2xl overflow-hidden">
                    <!-- 스피커/카메라 홈 -->
                    <div class="w-24 h-4 bg-slate-700 rounded-full mx-auto mb-2"></div>
                    
                    <!-- 폰 콘텐츠 구역 -->
                    <div class="flex-1 bg-slate-900 rounded-2xl p-4 flex flex-col justify-between relative overflow-hidden">
                        <!-- 스마트폰 탑바 -->
                        <div class="flex justify-between items-center text-[10px] text-slate-400">
                            <span>13:29</span>
                            <div class="space-x-1">
                                <i class="fa-solid fa-wifi"></i>
                                <i class="fa-solid fa-battery-three-quarters"></i>
                            </div>
                        </div>

                        <!-- 폰 스크린 홈 -->
                        <div class="flex-1 flex flex-col justify-center items-center text-center space-y-4">
                            <i class="fa-solid fa-car-side text-5xl text-teal-400 drop-shadow-lg"></i>
                            <h3 class="text-sm font-bold">My Connected App</h3>
                            <p class="text-slate-500 text-[10px]">수동/자동화 제어 대기 중</p>
                        </div>

                        <!-- 팝업 알림창 시뮬레이터 (실시간 노출) -->
                        <div id="phone-push" class="absolute top-8 left-2 right-2 bg-slate-800 border border-slate-700 rounded-lg p-3 shadow-2xl transform -translate-y-32 transition-transform duration-500 z-50">
                            <div class="flex items-center space-x-2 text-teal-400 text-xs font-semibold mb-1">
                                <i class="fa-solid fa-bell"></i>
                                <span>차량 원격 제어 완료</span>
                            </div>
                            <p id="push-msg" class="text-[10px] text-slate-200">2000: 원격 공조 제어가 완료되었습니다.</p>
                        </div>

                        <!-- 가상 하단 내비바 -->
                        <div class="w-24 h-1 bg-slate-600 rounded-full mx-auto mt-2"></div>
                    </div>
                </div>
            </section>
        </main>

        <!-- 뷰 갱신 전용 스크립트 -->
        <script>
            let currentSelectedTx = null;

            async function fetchTransactions() {
                try {
                    const res = await fetch('/api/v1/lms/transactions');
                    const txs = await res.json();
                    renderList(txs);
                    if (currentSelectedTx) {
                        const updated = txs.find(t => t.transaction_id === currentSelectedTx.transaction_id);
                        if (updated) {
                            selectTx(updated);
                        }
                    }
                } catch (e) {
                    console.error("데이터 로드 실패", e);
                }
            }

            function renderList(txs) {
                const listEl = document.getElementById('tx-list');
                if (txs.length === 0) {
                    listEl.innerHTML = '<div class="text-center text-slate-600 py-10">트랜잭션 로그가 없습니다.</div>';
                    return;
                }
                listEl.innerHTML = txs.map(tx => {
                    const isCompleted = tx.status === 'COMPLETED';
                    const isPending = tx.status === 'PENDING';
                    const badgeClass = isCompleted ? 'bg-emerald-500/20 text-emerald-400 border-emerald-500/20' : 
                                      isPending ? 'bg-amber-500/20 text-amber-400 border-amber-500/20' : 
                                      'bg-rose-500/20 text-rose-400 border-rose-500/20';
                    return `
                        <div onclick='selectAndLockTx(${JSON.stringify(tx)})' class="bg-slate-800/80 hover:bg-slate-800 border border-slate-700 p-3 rounded-lg cursor-pointer transition-all flex justify-between items-center ${currentSelectedTx?.transaction_id === tx.transaction_id ? 'border-teal-400 ring-2 ring-teal-400/20' : ''}">
                            <div class="space-y-1">
                                <div class="text-xs font-bold text-slate-300 flex items-center">
                                    <span class="bg-indigo-500/20 text-indigo-400 text-[10px] px-1.5 py-0.5 rounded mr-1.5">${tx.service}</span>
                                    VIN: ...${tx.vin.slice(-6)}
                                </div>
                                <div class="text-[10px] text-slate-500">ID: ${tx.transaction_id.slice(0, 8)}</div>
                            </div>
                            <div class="text-right">
                                <span class="border px-2 py-0.5 rounded-full text-[9px] font-semibold ${badgeClass}">
                                    Code ${tx.status_code || 'PENDING'}
                                </span>
                            </div>
                        </div>
                    `;
                }).join('');
            }

            function selectAndLockTx(tx) {
                currentSelectedTx = tx;
                selectTx(tx);
            }

            function selectTx(tx) {
                document.getElementById('empty-ladder').style.display = 'none';
                const diagram = document.getElementById('ladder-diagram');
                diagram.style.display = 'flex';

                // JSON 디테일 주입
                document.getElementById('tx-details').innerText = JSON.stringify(tx, null, 2);

                // 차량 상태 모니터 기입
                document.getElementById('car-eco').innerText = tx.profile.eco_type;
                document.getElementById('car-nad').innerText = tx.profile.nad_id;
                document.getElementById('car-action').innerText = tx.command;
                
                const resultEl = document.getElementById('car-result');
                resultEl.innerText = tx.status_code ? `${tx.status_code}: ${tx.status === 'COMPLETED' ? 'SUCCESS' : 'FAILURE'}` : 'PENDING';
                resultEl.className = tx.status === 'COMPLETED' ? 'text-emerald-400 font-bold' : tx.status === 'FAILED' ? 'text-rose-400 font-bold' : 'text-amber-400';

                // 사다리 스텝 렌더링
                const stepsEl = document.getElementById('sequence-steps');
                stepsEl.innerHTML = tx.steps.map((step, index) => {
                    const isRight = (step.from === 'App' && step.to === 'Server') || 
                                    (step.from === 'Server' && step.to === 'CCU') || 
                                    (step.from === 'CCU' && step.to === 'ECU');
                    const isReturn = (step.from === 'ECU' && step.to === 'CCU') || 
                                     (step.from === 'CCU' && step.to === 'Server') || 
                                     (step.from === 'Server' && step.to === 'App');
                    
                    let positionClass = '';
                    let lineDirectionClass = '';
                    
                    if (step.from === 'App' && step.to === 'Server') {
                        positionClass = 'left-[15%] w-[25%]';
                        lineDirectionClass = 'border-t-2 border-indigo-500 border-dashed';
                    } else if (step.from === 'Server' && step.to === 'CCU') {
                        positionClass = 'left-[40%] w-[30%]';
                        lineDirectionClass = 'border-t-2 border-yellow-500 border-dashed';
                    } else if (step.from === 'CCU' && step.to === 'ECU') {
                        positionClass = 'left-[70%] w-[20%]';
                        lineDirectionClass = 'border-t-2 border-emerald-500';
                    } else if (step.from === 'CCU' && step.to === 'Server') {
                        positionClass = 'left-[40%] w-[30%]';
                        lineDirectionClass = 'border-t-2 border-yellow-500 border-dashed';
                    } else if (step.from === 'Server' && step.to === 'App') {
                        positionClass = 'left-[15%] w-[25%]';
                        lineDirectionClass = 'border-t-2 border-teal-500';
                    }

                    const arrowIcon = isRight ? 'fa-arrow-right' : 'fa-arrow-left';

                    return `
                        <div class="relative h-10 w-full flex items-center">
                            <!-- 선 표시 -->
                            <div class="absolute h-0.5 ${positionClass} ${lineDirectionClass} flex justify-center items-center">
                                <span class="bg-slate-900 border border-slate-700 px-2 py-0.5 rounded text-[8px] font-semibold text-slate-300 transform -translate-y-4">
                                    ${step.msg} <i class="fa-solid ${arrowIcon}"></i>
                                </span>
                            </div>
                        </div>
                    `;
                }).join('');

                // 푸시 알림 핸들링
                const pushPopup = document.getElementById('phone-push');
                const pushMsg = document.getElementById('push-msg');
                if (tx.status_code && tx.vehicle_feedback) {
                    pushMsg.innerText = `${tx.status_code}: ${tx.vehicle_feedback}`;
                    pushPopup.style.transform = 'translateY(0px)';
                    
                    // 5초 뒤 소멸 연출
                    setTimeout(() => {
                        pushPopup.style.transform = 'translateY(-128px)';
                    }, 5000);
                } else {
                    pushPopup.style.transform = 'translateY(-128px)';
                }
            }

            // 1초 단위 실시간 대시보드 데이터 폴링
            setInterval(fetchTransactions, 1000);
            fetchTransactions();
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)