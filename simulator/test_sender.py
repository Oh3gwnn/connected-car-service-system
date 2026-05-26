import requests
import json
import time

# -----------------------------------------------------------------------------
# [글로벌 환경 설정]
# -----------------------------------------------------------------------------
BASE_URL = "http://127.0.0.1:8000/api/v1/control/vehicle"

# ANSI 터미널 색상 정의
RESET = "\033[0m"
BOLD = "\033[1m"
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
MAGENTA = "\033[35m"

# -----------------------------------------------------------------------------
# [테스트 시나리오 매트릭스 정의]
# -----------------------------------------------------------------------------
TEST_SCENARIOS = {
    "1": {
        "title": "원격 공조 가동 성공 (RSC_CLIMATE_SUCCESS - 2000)",
        "desc": "정상 개통된 아이오닉6(EV) 차량에 24.5°C 공조 기동 명령을 발송합니다.",
        "payload": {
            "vin": "KMHCT41BPJU123456",
            "command": "START_CLIMATE",
            "temperature": 24.5
        }
    },
    "2": {
        "title": "RDO 문 잠금 해제 성공 (VSS_UNLOCK_SUCCESS - 2041)",
        "desc": "정상 개통된 차량에 RDO 프로토콜을 이용해 도어 언락 명령을 수행합니다.",
        "payload": {
            "vin": "KMHCT41BPJU123456",
            "command": "UNLOCK_DOOR",
            "temperature": None
        }
    },
    "3": {
        "title": "RDO 문 잠금 성공 (VSS_LOCK_SUCCESS - 2040)",
        "desc": "정상 개통된 차량에 RDO 프로토콜을 이용해 도어 락 명령을 수행합니다.",
        "payload": {
            "vin": "KMHCT41BPJU123456",
            "command": "LOCK_DOOR",
            "temperature": None
        }
    },
    "4": {
        "title": "EV 차량 원격 시동 가드 차단 검증 (REQ_ECO_LIMIT - 4002)",
        "desc": "[Negative] EV 차량(아이오닉6)에 기계적 엔진 시동을 명령하여 서버 단에서 차단되는지 검증합니다.",
        "payload": {
            "vin": "KMHCT41BPJU123456",
            "command": "START_ENGINE",
            "temperature": None
        }
    },
    "5": {
        "title": "미개통 차량 제어 권한 차단 검증 (PROV_DEACTIVATED - 4031)",
        "desc": "[Negative] 데이터베이스에 존재하나 개통이 안 된 G80 차량에 명령을 내려 서버 가드 작동을 검증합니다.",
        "payload": {
            "vin": "KMHCT41BPJU888888",
            "command": "START_CLIMATE",
            "temperature": 23.0
        }
    },
    "6": {
        "title": "미등록 차대번호 제어 원천 차단 검증 (PROV_UNREGISTERED - 4030)",
        "desc": "[Negative] 아예 가입 데이터가 존재하지 않는 가짜 차대번호로 제어를 요청하여 원천 차단되는지 검증합니다.",
        "payload": {
            "vin": "KMHCT999999999999",
            "command": "LOCK_DOOR",
            "temperature": None
        }
    },
    "7": {
        "title": "CCU 내부 물리 온도 제한 가드 거부 검증 (CAN_TX_FAIL_TEMP - 7930)",
        "desc": "[Negative] 서버 가드는 뚫었으나 CCU 장치 한계 온도(35.0°C)를 초과해 차량 내부에서 거부 통보(MO)하는지 검증합니다.",
        "payload": {
            "vin": "KMHCT41BPJU123456",
            "command": "START_CLIMATE",
            "temperature": 35.0
        }
    }
}

# -----------------------------------------------------------------------------
# [통신 요청 핸들러]
# -----------------------------------------------------------------------------
def dispatch_request(choice: str):
    scenario = TEST_SCENARIOS.get(choice)
    if not scenario:
        print(f"\n{RED}❌ 올바르지 않은 번호입니다. 다시 선택해 주세요.{RESET}")
        return

    print("\n" + "="*80)
    print(f"{BOLD}{CYAN}🚀 [Dispatched] {scenario['title']}{RESET}")
    print(f"📄 {scenario['desc']}")
    print(f"📦 Payload: {json.dumps(scenario['payload'], indent=2)}")
    print("-"*80)

    try:
        response = requests.post(BASE_URL, json=scenario['payload'], timeout=5)
        
        # 응답 상태에 따른 하이라이팅 처리
        if response.status_code == 202:
            print(f"{GREEN}🟢 [Server Response: 202 Accepted]{RESET}")
            print(f"✨ 결과: {response.json().get('message')}")
            print(f"🔑 ID  : {response.json().get('transaction_id')}")
        else:
            print(f"{RED}🔴 [Server Response: {response.status_code} Error]{RESET}")
            try:
                print(f"⚠️ 원인: {response.json().get('detail')}")
            except:
                print(f"⚠️ 메시지: {response.text}")

    except requests.exceptions.ConnectionError:
        print(f"{RED}❌ [오류] FastAPI 서버가 구동 중이 아닙니다. 포트 8000번을 다시 확인해 주세요.{RESET}")
    except Exception as e:
        print(f"{RED}❌ [오류 발생] {str(e)}{RESET}")
    print("="*80 + "\n")

# -----------------------------------------------------------------------------
# [인터랙티브 CLI 메뉴]
# -----------------------------------------------------------------------------
def main():
    while True:
        print(f"{BOLD}{MAGENTA}=== TMS Interactive Test Scenario Generator ==={RESET}")
        for key, scenario in TEST_SCENARIOS.items():
            color = GREEN if int(key) <= 3 else RED if int(key) >= 4 else YELLOW
            print(f" [{key}] {color}{scenario['title']}{RESET}")
        print(" [Q] 테스트 종료 (Quit)")
        print(f"{MAGENTA}================================================={RESET}")
        
        choice = input("👉 테스트할 시나리오 번호를 선택하십시오: ").strip().upper()
        
        if choice == 'Q':
            print(f"\n{BOLD}👋 TMS 시나리오 테스트를 종료합니다. 수고하셨습니다!{RESET}\n")
            break
        
        dispatch_request(choice)
        time.sleep(1) # 모니터링 가시성을 위한 1초 대기

if __name__ == "__main__":
    main()

### 📂 사용 방법 및 테스트 실행법

'''
1. 백엔드 인프라가 잘 켜져 있는지 확인:
   * 도커(MQTT): `docker compose up -d`
   * 가상 차량 CCU: `python -m simulator.ccu`
   * FastAPI 백엔드 서버: `python main.py`
   * TMS 관제 웹 화면: 브라우저에서 `http://localhost:8000/tms` 접속

2. 요청기 기동 (새로운 터미널 창을 열어서 실행):
   python -m simulator.test_sender
'''