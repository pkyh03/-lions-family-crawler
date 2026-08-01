# 삼성 라이온즈 가족 대시보드 - 자동 갱신 크롤러

GitHub Actions 무료 스케줄러로 KBO 데이터를 주기적으로 긁어서
Supabase(`samsung-lions-family` 프로젝트)에 저장합니다.

## 갱신 주기
- **경기 시간대(17:00~23:59 KST)**: 5분 간격 (`live-game-poll.yml`)
- **그 외 시간**: 하루 2번, 09:00 / 18:00 KST (`daily-refresh.yml`)
- GitHub 무료 스케줄러 특성상 실제 실행은 몇 분 정도 밀릴 수 있습니다. "완전 실시간"이 아니라 "몇 분 지연되는 준실시간"으로 이해해 주세요.

## 설정 순서

### 1. GitHub 리포지토리 만들기
1. github.com에서 새 저장소 생성 (예: `lions-family-crawler`)
   - **Public**으로 만들면 Actions 무료 사용량 제한이 사실상 없습니다 (Private는 개인 계정 기준 월 2,000분 무료 — 이 정도 스케줄이면 충분합니다).
2. 이 폴더(`lions-crawler/`) 안의 모든 파일을 그 저장소에 push 합니다.

```bash
cd lions-crawler
git init
git add .
git commit -m "init: KBO crawler"
git branch -M main
git remote add origin https://github.com/<본인계정>/lions-family-crawler.git
git push -u origin main
```

### 2. Supabase 서비스 키 등록 (GitHub Secrets)
1. Supabase 대시보드 → `samsung-lions-family` 프로젝트 → **Project Settings → API**
2. **service_role** 키를 복사합니다. (⚠️ anon 키가 아닌 service_role 키입니다. 이 키는 RLS를 무시하고 쓰기 권한을 가지므로 절대 외부에 노출되면 안 됩니다 — GitHub Secrets에만 넣고 코드에 직접 적지 마세요.)
3. GitHub 저장소 → **Settings → Secrets and variables → Actions → New repository secret**
   - `SUPABASE_URL` = `https://eakttvuspuoydsuysasa.supabase.co`
   - `SUPABASE_SERVICE_ROLE_KEY` = (복사한 service_role 키)

### 3. 동작 확인
1. GitHub 저장소 → **Actions** 탭 → 좌측에서 `Lions Live Game Poll` 또는 `Lions Daily Refresh` 선택
2. **Run workflow** 버튼으로 수동 실행 → 로그에서 정상 동작 확인
3. 실패한다면 로그에 `TODO` 표시된 CSS 선택자 부분을 실제 KBO 페이지 구조에 맞게 수정해야 합니다 (아래 참고)

## ⚠️ 반드시 알아야 할 점 (중요)

KBO·삼성 라이온즈는 공식 오픈 API가 없어서, `crawler/kbo_crawler.py`는
**HTML 페이지를 직접 파싱(스크래핑)** 하는 방식입니다. 이로 인해:

- 사이트 개편 시 셀렉터가 깨질 수 있어 **주기적인 유지보수가 필요**합니다.
- 이 코드의 CSS 선택자(`table.tData`, `.tbGameSchedule` 등)는 **일반적인 구조를 가정한 초안**입니다. 실제 배포 전에 브라우저 개발자도구(F12)로 KBO 페이지를 열어 정확한 선택자로 한 번 조정해 주셔야 합니다.
- **출전선수 개인 기록(그날의 타율/이닝 등)**은 팀 단위 페이지보다 훨씬 구조가 복잡한 "게임 상세 박스스코어" 페이지를 파싱해야 해서, 이번 1차 버전에는 포함하지 않았습니다. 순위·일정·팀 스코어가 안정적으로 돌아간 다음 2차로 추가하는 걸 권장드립니다.
- 크롤링은 상대 서버에 부하를 주지 않도록 과도한 요청 빈도를 피해주세요 (현재 5분 간격은 무리 없는 수준입니다).

## 파일 구조
```
lions-crawler/
├── .github/workflows/
│   ├── live-game-poll.yml    # 경기시간대 5분 간격
│   ├── daily-refresh.yml     # 평시 하루 2회
│   └── deploy-pages.yml      # webapp/ 를 GitHub Pages로 배포
├── crawler/
│   ├── kbo_crawler.py        # 크롤러 본체
│   └── requirements.txt
├── webapp/                   # 가족용 PWA 대시보드 (정적 사이트)
│   ├── index.html
│   ├── app.js
│   ├── style.css
│   ├── config.js             # Supabase URL + anon key (공개돼도 안전)
│   ├── manifest.webmanifest
│   ├── sw.js
│   └── icons/
└── README.md
```

## 가족용 PWA 대시보드 (webapp/)

순위 · 오늘 경기 · 최근 결과/일정 · 직관 일정 · 선수단을 보여주는 모바일 대시보드입니다.
Supabase `standings`/`games`/`ticket_events`/`players` 테이블을 anon(읽기 전용) 키로 직접 조회합니다.
service_role 키는 여기 절대 넣지 않습니다 (쓰기는 크롤러만, GitHub Secrets로).

### 배포 (GitHub Pages)
1. 이 저장소를 GitHub에 push 하면 `deploy-pages.yml`이 `webapp/` 폴더를 자동으로 배포합니다.
2. GitHub 저장소 → **Settings → Pages** → Source를 **GitHub Actions**로 설정 (최초 1회).
3. 배포되면 `https://<계정>.github.io/<저장소명>/` 주소가 생깁니다.

### 가족 휴대폰에 설치 (PWA, 앱스토어 불필요)
- **iPhone(Safari)**: 위 주소 접속 → 공유 버튼 → **홈 화면에 추가**
- **Android(Chrome)**: 위 주소 접속 → 메뉴(⋮) → **앱 설치** (또는 자동으로 뜨는 설치 배너)

### 실시간 방송 연결에 대해
KBO 경기 온라인/모바일 중계권은 TVING이 독점 보유하고 있어, 오늘 경기 카드의 "TVING에서 시청" 버튼은
**TVING의 공식 KBO 페이지(`tving.com/sports/kbo`)로 이동**하는 링크입니다. TVING 앱이 설치돼 있으면
앱 링크로 앱이 열리고, 없으면 웹에서 바로 볼 수 있습니다. 방송 영상 자체를 앱에 끌어와 재생하는 방식은
저작권 문제가 되므로 하지 않았습니다.

방송 시청이 어려운 상황을 대비해, 오늘 경기가 진행 중일 때는 크롤러가 KBO 스코어보드에서 가져온
**이닝별 점수판(회별 스코어)**과 **주자/아웃 카운트 간이 표시**를 화면에 보여줍니다. 다만 주자 표시는
실제 라이브 경기로 검증하지 못한 휴리스틱이라 다음 실제 경기 때 값이 맞는지 한 번 확인해보시는 걸 권장합니다.

### 선수별 기록(박스스코어) 자동 수집에 대해
`crawler/kbo_crawler.py`가 경기 종료를 감지하면 KBO의 내부 박스스코어 API(`/ws/Schedule.asmx/GetBoxScoreScroll`)를
직접 호출해서 타자/투수 개인 기록을 `game_player_stats` 테이블에 자동으로 채웁니다. 사람이 직접 입력할 필요가 없습니다.
