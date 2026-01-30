# YOTTA LAB 웹사이트 배포 가이드 (Render)

## 1. GitHub에 코드 올리기

### 1-1. GitHub 가입 및 저장소 생성
1. [github.com](https://github.com) 가입
2. **New repository** 클릭
3. 이름 예: `yotta-lab`
4. **Create repository** (README 등 추가 안 해도 됨)

### 1-2. Git 설치 및 푸시
프로젝트 폴더에서 명령 프롬프트(PowerShell) 실행:

```bash
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/내아이디/yotta-lab.git
git push -u origin main
```

> `.env`와 `database.db`는 `.gitignore`에 있어 올라가지 않습니다 (보안상 올리면 안 됨).

---

## 2. Render에 배포

### 2-1. Render 가입
1. [render.com](https://render.com) 접속
2. **Get Started** → GitHub로 로그인

### 2-2. 웹 서비스 생성
1. **Dashboard** → **New +** → **Web Service**
2. GitHub 저장소 `yotta-lab` 연결 (권한 허용)
3. 설정:
   - **Name**: `yotta-lab` (원하는 이름)
   - **Region**: Singapore (한국에서 가장 가까움)
   - **Branch**: `main`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `uvicorn main:app --host 0.0.0.0 --port $PORT`
   - **Instance Type**: Free

4. **Environment** 탭에서 환경변수 추가:

| Key | Value |
|-----|-------|
| `SMTP_USER` | remylee@naver.com |
| `SMTP_PASSWORD` | (네이버 앱 비밀번호) |
| `BIZINFO_API_KEY` | (기업마당 API 키, 있으면) |

5. **Create Web Service** 클릭

### 2-3. 배포 완료
- 빌드가 끝나면 `https://yotta-lab.onrender.com` 형태의 URL이 생성됩니다.
- 첫 접속 시 응답이 느릴 수 있습니다 (무료 플랜 슬립 모드).

---

## 3. 참고 사항

- **무료 플랜**: 15분 미사용 시 슬립 → 첫 요청 시 재시작(30초~1분 소요)
- **DB**: SQLite는 무료 플랜에서 재시작 시 초기화될 수 있음
- **admin 비밀번호**: 첫 실행 시 시드 데이터로 설정됨 (admin / 12345 등)
