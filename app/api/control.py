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
    command: str = Field(..., description="제어 명령 (START_CLIMATE, RDO_LOCK, RDO_UNLOCK, START_ENGINE)", examples=["START_CLIMATE"])
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
    
    # 🌟 [실무 사양 고도화] RDO로 도어 명령 통일 및 개통/물리 레지스터 상태 분리 설계
    tx_log = {
        "transaction_id": tx_id,
        "vin": request.vin,
        "service": "RSC" if "CLIMATE" in request.command or "ENGINE" in request.command else "RDO",
        "command": "RDO_LOCK" if request.command == "LOCK_DOOR" else "RDO_UNLOCK" if request.command == "UNLOCK_DOOR" else request.command,
        "status": "PENDING",
        "status_code": None,
        
        # 1. 개통 정보 DB 스키마 분리
        "provision_state": {
            "vin": request.vin,
            "nad_id": profile["nad_id"] if profile else "UNREGISTERED",
            "carrier": profile["carrier"] if profile else "UNKNOWN",
            "eco_type": profile["eco_type"] if profile else "UNKNOWN",
            "is_activated": profile["is_activated"] if profile else False
        },
        
        # 2. 차량 ECU 실제 레지스터 상태 스키마 분리
        "vehicle_state": {
            "engine_status": 0,          # 0: STANDBY, 1: DRIVE_READY
            "rdo_state": {
                "lock": 1,               # 0: UNLOCKED, 1: LOCKED
                "door_open": 0           # 0: ALL_CLOSED
            },
            "climate_state": {
                "active": 0,             # 0: OFF, 1: ACTIVE
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
    
    # 2. 개통 여부 검증 (Provisioning Check)
    if not profile:
        tx_log["status"] = "FAILED"
        tx_log["status_code"] = 4030
        tx_log["vehicle_feedback"] = "등록되지 않은 차대번호 요청"
        LMS_TRANSACTIONS.append(tx_log)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="4030: 미등록된 단말 정보의 접근입니다."
        )
    
    if not profile["is_activated"]:
        tx_log["status"] = "FAILED"
        tx_log["status_code"] = 4031
        tx_log["vehicle_feedback"] = "미개통 단말기로 제어 차단됨"
        tx_log["steps"].append({
            "from": "Server", "to": "App", "msg": "BLOCKED_BY_PROVISIONING", "timestamp": time.time()
        })
        LMS_TRANSACTIONS.append(tx_log)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="4031: 개통이 해지되거나 만료된 차량 단말기입니다."
        )

    # 3. 차종별(ECO Type) 지원 한계 가드 처리
    if profile["eco_type"] == "EV" and request.command == "START_ENGINE":
        tx_log["status"] = "FAILED"
        tx_log["status_code"] = 4002
        tx_log["vehicle_feedback"] = "EV 차종에 시동 명령 차단"
        LMS_TRANSACTIONS.append(tx_log)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="4002: EV 차량은 기계적인 엔진 시동(START_ENGINE)을 지원하지 않습니다."
        )

    # 4. 공조 제어 온도 파라미터 체크
    if "CLIMATE" in request.command and request.temperature is None:
        tx_log["status"] = "FAILED"
        tx_log["status_code"] = 4000
        tx_log["vehicle_feedback"] = "공조 설정 온도 누락"
        LMS_TRANSACTIONS.append(tx_log)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="4000: 공조 기동에는 목표 온도가 필수입니다."
        )

    # 5. 모든 서버 가드를 통과하면, 무선 릴레이 시도 전 LMS 히스토리에 먼저 등록하여 대기상태 노출
    mqtt_topic = f"ccs/vehicle/{request.vin}/control"
    
    # 🌟 [실무 사양 반영] RDO 명령어로 표준화 변환
    trans_command = "RDO" if "DOOR" in request.command else request.command
    mqtt_payload = {
        "transaction_id": tx_id,
        "command": trans_command,
        "temperature": request.temperature
    }
    
    tx_log["steps"].append({
        "from": "Server", "to": "CCU", "msg": f"PUBLISH_MQTT ({trans_command})", "timestamp": time.time()
    })
    
    LMS_TRANSACTIONS.append(tx_log)
    
    # 6. 실질적 무선 메시지 전송 시도 (동기식 보장)
    try:
        publish_mqtt_message(mqtt_topic, mqtt_payload)
    except HTTPException as he:
        tx_log["status"] = "FAILED"
        tx_log["status_code"] = 7000
        tx_log["vehicle_feedback"] = "무선 전송 실패 (MQTT 브로커 끊김)"
        tx_log["steps"].append({
            "from": "Server", "to": "App", "msg": "BROKER_CONNECTION_ERROR", "timestamp": time.time()
        })
        raise he
    
    return {
        "status": "ACCEPTED",
        "transaction_id": tx_id,
        "message": "제어 무선 호출이 시작되었습니다."
    }