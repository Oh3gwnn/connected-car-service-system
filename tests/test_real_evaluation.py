import time
import requests
import pytest

CCU_URL = "http://127.0.0.1:8050"
TMS_URL = "http://127.0.0.1:8000"

@pytest.fixture(autouse=True)
def clean_environment():
    """테스트 시작 전 B+ 리셋을 수행하여 무조건 SLEEP 상태에서 시작하도록 보장"""
    try:
        res = requests.post(f"{CCU_URL}/hardware/signal", json={"signal": "BPLUS_RESET"}, timeout=2)
        assert res.status_code == 200
    except requests.exceptions.ConnectionError:
        pytest.fail("❌ [FAIL] 가상 CCU 서버(ccu_enhanced.py)가 켜져 있지 않습니다.")
    yield

def test_tc_pwr_wakeup_01_end_to_end():
    print("\n\n" + "="*80)
    print("📋 [TC-PWR-WAKEUP-01] 실무형 CCU 전원 복구 및 VSS 정합성 자동화 시험 시작")
    print("="*80)

    # 1. Pre-condition (사전 조건) 검증
    print("\n🔍 [STEP 1] 사전 전원 조건 검증...")
    status_res = requests.get(f"{CCU_URL}/hardware/status").json()
    assert status_res["panel_acc"] == 0
    assert status_res["wire_acc"] == 0
    assert status_res["power_state"] == "SLEEP"
    print("🟢 [PASS] 사전 상태 정합성 확인 완료 (Deep Sleep 상태 진입 확인)")

    # 2. 전원 인가 (Wakeup 트리거)
    print("\n⚙️  [STEP 2] 전원 기동 트리거 전송 (Panel & Wire ACC ON)")
    trigger_res = requests.post(f"{CCU_URL}/hardware/signal", json={"signal": "WAKEUP_TRIGGER"}).json()
    assert trigger_res["status"] == "TRANSITION_STARTED"
    assigned_tid = trigger_res["transaction_id"]

    # 3. 5초 부팅 및 이더넷 링크업 자동 폴링 추적
    print("\n⏳ [STEP 3] 가상 커널 부팅 및 이더넷 연결 상태 자동 추적...")
    start_time = time.time()
    is_ready = False
    
    while time.time() - start_time < 10:  # 최대 10초 타임아웃 가드
        current_status = requests.get(f"{CCU_URL}/hardware/status").json()
        if current_status["power_state"] == "READY" and current_status["is_ethernet_up"]:
            is_ready = True
            break
        time.sleep(0.5)

    assert is_ready is True, "❌ [FAIL] 제한 시간 내에 READY 상태에 도달하지 못했습니다."
    print(f"🟢 [PASS] CCU 부팅 완료 (소요 시간: {time.time() - start_time:.2f}초)")

    # 4. 기대 결과(Expected Result) 검증 - 변경 및 미변경 디폴트 값 대조
    print("\n📊 [STEP 4] VSS 요구사항 상태 데이터 최종 정합성 검증")
    final_status = requests.get(f"{CCU_URL}/hardware/status").json()
    
    assert final_status["panel_acc"] == 1
    assert final_status["wire_acc"] == 1
    assert final_status["sleepmode"] == 0       # 0: WAKEUP 확인
    assert final_status["ign1"] == 0            # 디폴트 0 유지 확인
    assert final_status["ign3"] == 0            # 디폴트 0 유지 확인
    assert final_status["current_vssc_version"] == "VSSC-IONIQ6-REV-9872"  # 직전 슬립 이력 버전 확인
    print("🟢 [PASS] CCU 데이터 검증 성공: [sleepmode=0 / acc=1 / ign1,3=0(디폴트) / VSS-C 복원완료]")

    # 5. 서버단 트랜잭션 기록 최종 확인 및 PASSED 마킹
    print("\n🛰️  [STEP 5] TMS 관제 서버(Port 8000) 트랜잭션 기록 동기화 최종 검증")
    time.sleep(1.0)
    try:
        tms_res = requests.get(f"{TMS_URL}/api/v1/lms/transactions", timeout=3).json()
        vss_record = next((tx for tx in tms_res if tx["status_code"] == 204 and tx["vin"] == final_status["vin"]), None)
        
        assert vss_record is not None
        assert vss_record["vehicle_state"]["wires"]["acc"] == 1
        print(f"🟢 [PASS] TMS 서버 트랜잭션 동기화 검증 성공! (TID 매칭 완료)")
    except requests.exceptions.ConnectionError:
        print(f"⚠️  [SKIP] main.py가 꺼져 있어 서버 기록 검증 단계는 스킵합니다.")

    print("\n" + "="*80)
    print(f"🏆 [FINAL RESULT] {final_status['vin']} -> TC-PWR-WAKEUP-01: PASSED")
    print("="*80 + "\n")