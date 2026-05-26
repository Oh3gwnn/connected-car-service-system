import json
import uuid
import time
import os
from typing import Optional
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
import paho.mqtt.publish as publish

router = APIRouter(prefix="/control", tags=["Remote Control"])

# -----------------------------------------------------------------------------
# [환경별 MQTT 브로커 주소 자동 해결 설계]
# -----------------------------------------------------------------------------
IS_CONTAINER = os.getenv("IS_CONTAINER", os.path.exists('/.dockerenv'))
DEFAULT_HOST = "mqtt" if IS_CONTAINER else "127.0.0.1"
MQTT_BROKER_HOST = os.getenv("MQTT_BROKER_HOST", DEFAULT_HOST)

# -----------------------------------------------------------------------------
# [Pydantic DTO 데이터 규격]
# -----------------------------------------------------------------------------
class ControlRequest(BaseModel):
    vin: str = Field(..., description="차량 고유 차대번호", examples=["KMHCT41BPJU123456"])
    command: str = Field(..., description="제어 명령 (RSC_START_CLIMATE, RSC_START_ENGINE, RDO_LOCK, RDO_UNLOCK)", examples=["RSC_START_CLIMATE"])
    temperature: Optional[float] = Field(None, description="설정 온도 (공조 제어 필수)", examples=[23.5])

# -----------------------------------------------------------------------------
# [가상 차량 데이터 조회 유틸]
# -----------------------------------------------------------------------------
def get_vehicle_profile(vin: str):
    from app.main import VEHICLE_REGISTRY
    return VEHICLE_REGISTRY.get(vin)

# -----------------------------------------------------------------------------
# [MQTT 메시지 전송 로직]
# -----------------------------------------------------------------------------
def publish_mqtt_message(topic: str, payload: dict):
    try:
        publish.single(
            topic,
            payload=json.dumps(payload),
            hostname=MQTT_BROKER_HOST,
            port=1883,
            keepalive=60
        )
        print(f"🟢 [MQTT 송신 완료] 토픽: {topic}")
    except Exception as e:
        error_detail = f"MQTT 전송 실패 (브로커: {MQTT_BROKER_HOST}): {str(e)}"
        print(f"❌ [MQTT 에러] {error_detail}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"차량 제어 장치(MQTT 브로커 주소: {MQTT_BROKER_HOST})와의 통신에 실패했습니다. 원인: {str(e)}"
        )

# -----------------------------------------------------------------------------
# [원격 제어 엔드포인트]
# -----------------------------------------------------------------------------
@router.post("/vehicle", status_code=status.HTTP_202_ACCEPTED)
async def remote_control_vehicle(request: ControlRequest):
    from app.main import LMS_TRANSACTIONS
    
    tx_id = str(uuid.uuid4())
    current_time = time.time()
    
    # [데이터 유효성 가드 확인 전 프로파일 조회]
    profile = get_vehicle_profile(request.vin)
    
    # 🌟 메모리상의 최근 완료된 트랜잭션에서 이 차량의 가장 최신 전원(ign_state) 및 잠금 상태 계승 조회
    current_ign_state = 0  # Default PANEL_OFF
    current_lock_state = 1 # Default LOCKED
    for t in reversed(LMS_TRANSACTIONS):
        if t["vin"] == request.vin and t["status"] == "COMPLETED":
            current_ign_state = t["vehicle_state"]["ign_state"]
            current_lock_state = t["vehicle_state"]["rdo_state"]["lock"]
            break

    # 🌟 [피드백 반영] RSC / RDO 서비스 분류 버그 전면 수정
    # 구형 명령명(START_CLIMATE)이 들어오더라도 단어가 포함되어 있다면 완벽하게 RSC로 바인딩합니다.
    is_rsc = "RSC" in request.command or "CLIMATE" in request.command or "ENGINE" in request.command

    tx_log = {
        "transaction_id": tx_id,
        "vin": request.vin,
        "service": "RSC" if is_rsc else "RDO",
        "command": request.command,
        "status": "PENDING",
        "status_code": None,
        
        # 1. 개통 마스터 정보 DB 스키마
        "provision_state": {
            "vin": request.vin,
            "nad_id": profile["nad_id"] if profile else "UNREGISTERED",
            "carrier": profile["carrier"] if profile else "UNKNOWN",
            "eco_type": profile["eco_type"] if profile else "UNKNOWN",
            "is_activated": profile["is_activated"] if profile else False
        },
        
        # 2. 차량 ECU 실제 레지스터 및 전원 와이어링 상태 스키마
        "vehicle_state": {
            "ign_state": current_ign_state,  # 0:PANEL_OFF, 1:ACC, 2:IGN_ON, 3:KEY_START
            "wires": {
                "b1": 1,                     
                "acc": 1 if current_ign_state >= 1 else 0,
                "ign1": 1 if current_ign_state >= 2 else 0
            },
            "rdo_state": {
                "lock": current_lock_state,   
                "door_open": 0               
            },
            "climate_state": {
                "active": 1 if current_ign_state == 3 else 0,
                "target_temp": request.temperature if "CLIMATE" in request.command else 22.0
            }
        },
        
        "profile": profile or {},
        "vehicle_feedback": None,
        "steps": [
            {
                "from": "App", 
                "to": "Server", 
                "msg": f"MRC-A {request.command} REQUEST" + (f" ({request.temperature}°C)" if request.temperature else ""), 
                "timestamp": current_time
            }
        ]
    }
    
    # 2. 개통 여부 검증 (Provisioning Check) -> 에러코드 4000 차단
    if not profile:
        tx_log["status"] = "FAILED"
        tx_log["status_code"] = 4000
        tx_log["vehicle_feedback"] = "4000: 미등록 차대번호 접근 차단"
        LMS_TRANSACTIONS.append(tx_log)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="4000: 미등록된 단말 정보의 접근입니다."
        )
    
    if not profile["is_activated"]:
        tx_log["status"] = "FAILED"
        tx_log["status_code"] = 4000
        tx_log["vehicle_feedback"] = "4000: 미개통 가입자 제어 차단"
        tx_log["steps"].append({
            "from": "Server", "to": "App", "msg": "BLOCKED_BY_PROVISIONING", "timestamp": time.time()
        })
        LMS_TRANSACTIONS.append(tx_log)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="4000: 개통이 해지되거나 만료된 차량 단말기입니다."
        )

    # 3. [차량 기능 안전 가드] 시동 중(KEY_START) RDO 차단 로직
    if "RDO" in request.command and current_ign_state == 3:
        tx_log["status"] = "FAILED"
        tx_log["status_code"] = 4000
        tx_log["vehicle_feedback"] = "4000: KEY_START 상태 원격 문 제어(RDO) 제한"
        LMS_TRANSACTIONS.append(tx_log)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="4000: 차량이 KEY_START (엔진구동) 중이므로 안전을 위해 RDO 제어를 차단합니다."
        )

    # 4. 차종별(ECO Type) 지원 한계 가드 처리 -> 에러코드 4000으로 통일
    if profile["eco_type"] == "EV" and "ENGINE" in request.command:
        tx_log["status"] = "FAILED"
        tx_log["status_code"] = 4000
        tx_log["vehicle_feedback"] = "4000: EV 차종 원격 엔진시동 가드 차단"
        LMS_TRANSACTIONS.append(tx_log)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="4000: EV 차량은 기계적인 엔진 시동(RSC_START_ENGINE)을 지원하지 않습니다."
        )

    # 5. 공조 제어 온도 파라미터 체크 -> 에러코드 4000으로 통일
    if "CLIMATE" in request.command and request.temperature is None:
        tx_log["status"] = "FAILED"
        tx_log["status_code"] = 4000
        tx_log["vehicle_feedback"] = "4000: 공조 설정 온도 파라미터 누락"
        LMS_TRANSACTIONS.append(tx_log)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="4000: 공조 기동에는 목표 온도가 필수입니다."
        )

    # 6. 모든 서버 가드를 통과하면, 무선 릴레이 시도 전 LMS 히스토리에 먼저 등록하여 대기상태 노출
    mqtt_topic = f"ccs/vehicle/{request.vin}/control"
    
    # RDO 명칭 세부 통일성 가드
    trans_command = "RDO" if "RDO" in request.command or "DOOR" in request.command else request.command
    mqtt_payload = {
        "transaction_id": tx_id,
        "command": trans_command,
        "temperature": request.temperature
    }
    
    tx_log["steps"].append({
        "from": "Server", "to": "CCU", "msg": f"PUBLISH_MQTT ({trans_command})", "timestamp": time.time()
    })
    
    LMS_TRANSACTIONS.append(tx_log)
    
    # 7. 실질적 무선 메시지 전송 시도 -> 브로커 연결 오류는 7000으로 통일
    try:
        publish_mqtt_message(mqtt_topic, mqtt_payload)
    except HTTPException as he:
        tx_log["status"] = "FAILED"
        tx_log["status_code"] = 7000
        tx_log["vehicle_feedback"] = "7000: 무선 전송 실패 (MQTT 브로커 끊김)"
        tx_log["steps"].append({
            "from": "Server", "to": "App", "msg": "BROKER_CONNECTION_ERROR", "timestamp": time.time()
        })
        raise he
    
    return {
        "status": "ACCEPTED",
        "transaction_id": tx_id,
        "message": "제어 무선 호출이 시작되었습니다."
    }