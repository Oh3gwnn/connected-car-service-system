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
# [테스트 시나리오 매트릭스 정의] -> RSC_STOP 추가 및 동적 온도 입력 구조
# -----------------------------------------------------------------------------
TEST_SCENARIOS = {
    "1": {
        "title": "원격 공조 가동 성공 (RSC_START_CLIMATE - 200)",
        "desc": "정상 개통된 아이오닉6(EV) 차량에 지정 온도로 공조 기동 명령을 발송합니다. (변동 시 204 VSS ➔ 200 완료 / 미변동 시 200 완료)",
        "payload": {
            "vin": "KMHCT41BPJU123456",
            "command": "RSC_START_CLIMATE",
            "temperature": 24.5
        }
    },
    "2": {
        "title": "RDO 문 잠금 해제 성공 (RDO_UNLOCK - 200)",
        "desc": "정상 개통된 차량에 RDO 프로토콜을 이용해 도어 언락 명령을 수행합니다. (lock: 0 으로 변동 시 204 VSS ➔ 200 완료)",
        "payload": {
            "vin": "KMHCT41BPJU123456",
            "command": "RDO_UNLOCK",
            "temperature": None
        }
    },
    "3": {
        "title": "RDO 문 잠금 성공 (RDO_LOCK - 200)",
        "desc": "정상 개통된 차량에 RDO 프로토콜을 이용해 도어 락 명령을 수행합니다. (lock: 1 로 변동 시 204 VSS ➔ 200 완료)",
        "payload": {
            "vin": "KMHCT41BPJU123456",
            "command": "RDO_LOCK",
            "temperature": None
        }
    },
    "4": {
        "title": "EV 차량 원격 시동 가드 차단 검증 (REQ_BLOCKED - 4000)",
        "desc": "[Negative] EV 차량(아이오닉6)에 기계적 엔진 시동을 명령하여 서버 단에서 사전 차단(Code 4000)되는지 검증합니다.",
        "payload": {
            "vin": "KMHCT41BPJU123456",
            "command": "RSC_START_ENGINE",
            "temperature": None
        }
    },
    "5": {
        "title": "미개통 차량 제어 권한 차단 검증 (PROV_DEACTIVATED - 4000)",
        "desc": "[Negative] 데이터베이스에 존재하나 개통이 안 된 G80 차량에 명령을 내려 서버 가드 작동(Code 4000)을 검증합니다.",
        "payload": {
            "vin": "KMHCT41BPJU888888",
            "command": "RSC_START_CLIMATE",
            "temperature": 23.0
        }
    },
    "6": {
        "title": "미등록 차대번호 제어 원천 차단 검증 (PROV_UNREGISTERED - 4000)",
        "desc": "[Negative] 가입 데이터가 존재하지 않는 차대번호 요청을 서버 가드(Code 4000)에서 즉각 파기하는지 검증합니다.",
        "payload": {
            "vin": "KMHCT999999999999",
            "command": "RDO_LOCK",
            "temperature": None
        }
    },
    "7": {
        "title": "CCU 내부 B-CAN 온도 제한 거부 검증 (CAN_TX_FAIL_TEMP - 7000)",
        "desc": "[Negative] 서버 가드는 통과했으나 CCU 장치 한계 온도(35.0°C)를 초과해 차량 내부에서 거부 통보(Code 7000)하는지 검증합니다.",
        "payload": {
            "vin": "KMHCT41BPJU123456",
            "command": "RSC_START_CLIMATE",
            "temperature": 35.0
        }
    },
    "8": {
        "title": "KEY_START(시동중) 상태 RDO 도어 제어 안전 차단 검증 (REQ_BLOCKED - 4000)",
        "desc": "[Functional Safety] 차량 전원이 3 (KEY_START: 시동 기동 중)인 상태에서 RDO 문 제어 시도 시 안전을 위해 서버에서 사전에 긴급 거부하는 시나리오입니다.",
        "payload": {
            "vin": "KMHCT41BPJU123456",
            "command": "RDO_UNLOCK",
            "temperature": None
        }
    },
    "9": {
        "title": "원격 공조/시동 정지 성공 (RSC_STOP_CLIMATE - 200)",
        "desc": "구동 중인 공조 및 전원을 오프하여 PANEL_OFF 상태로 초기화시킵니다. (ign_state: 0, acc: 0, ign1: 0 천이)",
        "payload": {
            "vin": "KMHCT41BPJU123456",
            "command": "RSC_STOP_CLIMATE",
            "temperature": None
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

    # 🌟 [개선] 1번 공조 가동 제어 시 온도를 터미널에서 즉석 동적 입력받도록 수정
    if choice == "1":
        print(f"\n{BOLD}{YELLOW}🌡️  설정 온도 가변 입력 모드 (상태 천이 검증 목적){RESET}")
        temp_input = input("👉 설정할 목표 온도를 입력하세요 (16.0 ~ 30.0) [기본값: 24.5]: ").strip()
        if temp_input:
            try:
                temp_val = float(temp_input)
                if 16.0 <= temp_val <= 30.0:
                    scenario["payload"]["temperature"] = temp_val
                    scenario["title"] = f"원격 공조 가동 성공 (RSC_START_CLIMATE - {temp_val}°C)"
                else:
                    print(f"{YELLOW}⚠️  허용 범위를 벗어났습니다. 기본값인 24.5°C로 전송합니다.{RESET}")
            except ValueError:
                print(f"{YELLOW}⚠️  실수/정수 값이 아닙니다. 기본값인 24.5°C로 전송합니다.{RESET}")

    print("\n" + "="*80)
    print(f"{BOLD}{CYAN}🚀 [Dispatched] {scenario['title']}{RESET}")
    print(f"📄 {scenario['desc']}")
    print(f"📦 Payload: {json.dumps(scenario['payload'], indent=2)}")
    print("-"*80)

    try:
        response = requests.post(BASE_URL, json=scenario['payload'], timeout=5)
        
        if response.status_code == 202:
            print(f"{GREEN}🟢 [Server Response: 202 Accepted]{RESET}")
            print(f"✨ 결과: {response.json().get('message')}")
            print(f"🔑 ID  : {response.json().get('transaction_id')}")
        else:
            print(f"{RED}🔴 [Server Response: {response.status_code} Error]{RESET}")
            try:
                print(f"⚠️  원인: {response.json().get('detail')}")
            except:
                print(f"⚠️  메시지: {response.text}")

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
            color = GREEN if int(key) <= 3 or int(key) == 9 else RED if int(key) >= 4 else YELLOW
            print(f" [{key}] {color}{scenario['title']}{RESET}")
        print(" [Q] 테스트 종료 (Quit)")
        print(f"{MAGENTA}================================================={RESET}")
        
        choice = input("👉 테스트할 시나리오 번호를 선택하십시오: ").strip().upper()
        
        if choice == 'Q':
            print(f"\n{BOLD}👋 TMS 시나리오 테스트를 종료합니다. 수고하셨습니다!{RESET}\n")
            break
        
        dispatch_request(choice)
        time.sleep(1)

if __name__ == "__main__":
    main()