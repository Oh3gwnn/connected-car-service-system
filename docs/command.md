0. 도커
docker compose up -d
docker compose down

1. 차량 대기 (CCU 가동)
python -m simulator.ccu
// http://127.0.0.1:8000/docs

2. fastAPI 서버 가동
uvicorn app.main:app --reload
// http://127.0.0.1:8000/lms

