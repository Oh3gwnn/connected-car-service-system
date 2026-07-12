0. 도커
docker compose up -d
docker compose down

1. 차량 대기 (CCU 가동)
python -m simulator.ccu
python -m simulator.ccu_enhanced
uvicorn simulator.ccu:app --port 8050 --reload
// http://127.0.0.1:8000/docs

2. fastAPI 서버 가동
uvicorn app.main:app --reload
uvicorn app.main:app --port 8000 --reload
// http://127.0.0.1:8000/tms

kill -9 $(lsof -t -i:8050)

pytest -s -v tests/test_power_performance.py

uvicorn v2x_project.simulator:app --port 8050 --reload