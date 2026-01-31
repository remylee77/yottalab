# YOTTA LAB 웹사이트 배포 가이드 (Render)

## 1. GitHub에 코드 올리기

### 1-1. GitHub 저장소 생성
1. [github.com](https://github.com) 로그인
2. **New repository** 클릭
3. **Repository name**: `yottalab` (폴더명과 동일)
4. **Public** 선택
5. **Create repository** (README 추가 안 해도 됨)

### 1-2. Git 설치 및 푸시
프로젝트 폴더(`홈페이지`)에서 PowerShell 또는 명령 프롬프트 실행:

```bash
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/remylee77/yottalab.git
git push -u origin main
```

- **아이디/비밀번호**: GitHub 로그인 계정 (또는 Personal Access Token)
- `.env`, `database.db`는 `.gitignore`에 있어 업로드되지 않습니다 (보안상 필요)

---

## 2. Render에 배포

### 2-1. Render 가입
1. [render.com](https://render.com) 접속
2. **Get Started** → GitHub로 로그인

### 2-2. 웹 서비스 생성
1. **Dashboard** → **New +** → **Web Service**
2. GitHub 저장소 `remylee77/yottalab` 연결 (권한 허용)
3. 설정:
   - **Name**: `yottalab` (저장소명과 맞추면 좋음)
   - **Region**: Singapore (한국에서 가장 가까움)
   - **Branch**: `main`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `uvicorn main:app --host 0.0.0.0 --port $PORT --workers 1`
   - **Health Check Path**: `/health` (Settings에서 설정)
   - **Instance Type**: Free

4. **Environment** 탭에서 환경변수 추가:

| Key | Value |
|-----|-------|
| `SMTP_USER` | remylee@naver.com |
| `SMTP_PASSWORD` | (네이버 앱 비밀번호) |
| `BIZINFO_API_KEY` | (기업마당 API 키, 있으면) |

5. **Create Web Service** 클릭

### 2-3. 배포 완료
- 빌드가 끝나면 `https://yottalab.onrender.com` 형태의 URL이 생성됩니다.
- 첫 접속 시 응답이 느릴 수 있습니다 (무료 플랜 슬립 모드).

---

## 3. 문제 해결

### GitHub 푸시 에러
- **Author identity unknown**:  
  `git config --global user.email "이메일"`  
  `git config --global user.name "이름"`
- **403 / 권한 거부**: Personal Access Token 사용 (GitHub → Settings → Developer settings → Personal access tokens)
- **Repository not found**: `remylee77/yottalab` 저장소가 생성되었는지 확인

### Render 배포 에러
- **빌드 실패**: `requirements.txt`에 필요한 패키지 모두 있는지 확인
- **앱 안 뜸**: 로그 확인 (Render Dashboard → 해당 서비스 → Logs)
- **/health** 접속해 `{"status":"ok"}` 나오면 정상

### "Application loading" 계속 반복
1. **Render Dashboard** → 해당 서비스 → **Logs** 탭에서 에러 메시지 확인
2. **Settings** → **Health Check Path**를 `/health`로 설정 (render.yaml에 이미 포함)
3. **Build & Deploy** → **Manual Deploy** → **Clear build cache & deploy** 실행
4. 로그에 `[STARTUP ERROR]`가 있으면 해당 오류 수정 후 재배포

## 4. 참고 사항

- **무료 플랜**: 15분 미사용 시 슬립 → 첫 요청 시 재시작(30초~1분 소요)
- **DB**: SQLite는 무료 플랜에서 재시작 시 초기화될 수 있음
- **admin 비밀번호**: 첫 실행 시 시드 데이터로 설정됨 (admin / 12345 등)
