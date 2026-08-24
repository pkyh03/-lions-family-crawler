"""
삼성 라이온즈 가족 대시보드 - KBO 데이터 크롤러

역할:
  1) KBO 팀 순위 페이지를 읽어 standings 테이블 갱신
  2) 삼성 라이온즈 일정/결과 페이지를 읽어 games 테이블 갱신
     (오늘 경기가 있으면 이닝별 스코어보드 · 주자/아웃 등 진행 상황까지 함께 반영)
  3) 경기가 진행 중이거나 끝나면 KBO 박스스코어 API에서 타자/투수 개인 기록을 읽어
     game_player_stats 테이블에 자동 반영 (진행 중에는 결승타 등 종료 확정 값은 제외)
  4) 박스스코어에 처음 보는 이름이 나오면(콜업) KBO 선수 검색으로 즉시 프로필까지
     채워 players 테이블에 자동 등록하고, 한동안(기본 20일) 출전이 없으면 비활성 처리
     - 로스터를 한 번 등록해두고 방치하지 않고 실제 출전 기록 기준으로 계속 최신화

주의:
  - KBO/삼성 라이온즈는 공식 오픈 API를 제공하지 않아 HTML/내부 AJAX 응답을 직접 파싱합니다.
    사이트 개편 시 셀렉터/엔드포인트가 깨질 수 있어, 실패해도 예외를 던지지 않고
    "이번 실행은 건너뜀 + 로그만 남김" 방식으로 안전하게 동작하도록 작성했습니다.
  - 아래 셀렉터/엔드포인트는 2026-08-01 기준 실제 koreabaseball.com 마크업과
    네트워크 요청을 브라우저로 직접 확인해서 맞춘 값입니다. 사이트 개편 시 재확인 필요.
  - 일정/결과는 ScoreBoard.aspx(파라미터 없이 요청하면 서버 기준 "오늘"만 보여줌)를 사용합니다.
    이전/다음 날짜 이동은 __doPostBack 기반이라 requests로 재현할 수 없어 오늘 하루치만 지원합니다.
  - 주자(1·2·3루) 재현 여부는 "비어있는 베이스 이미지 파일명과 다르면 주자 있음"이라는
    휴리스틱으로 판단합니다. 실제 라이브 경기로 검증하지 못했으니, 다음에 삼성 경기가
    실제로 진행 중일 때 값이 정확한지 한 번 확인해보는 걸 권장합니다.
"""

import os
import sys
import re
import json
import time
import datetime
import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# 환경 변수 (GitHub Actions Secrets에서 주입)
# ---------------------------------------------------------------------------
SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

HEADERS_SB = {
    "apikey": SUPABASE_SERVICE_ROLE_KEY,
    "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
    "Content-Type": "application/json",
}
HEADERS_WEB = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
}
# KBO 내부 .asmx 웹서비스(GetScoreBoardScroll/GetBoxScoreScroll)는 jQuery의 기본
# application/x-www-form-urlencoded 바디 + X-Requested-With 헤더가 없으면 401을 반환한다.
HEADERS_WS = {
    **HEADERS_WEB,
    "X-Requested-With": "XMLHttpRequest",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
}

TEAM_NAME_MAP = {
    "LG": "LG 트윈스", "KT": "KT 위즈", "SSG": "SSG 랜더스", "NC": "NC 다이노스",
    "두산": "두산 베어스", "KIA": "KIA 타이거즈", "삼성": "삼성 라이온즈",
    "롯데": "롯데 자이언츠", "한화": "한화 이글스", "키움": "키움 히어로즈",
}
# KBO 내부 팀 코드 (gameId 조합 및 .asmx 호출에 필요). koreabaseball.com의
# 엠블럼 이미지 파일명(emblem_XX.png)과 실제 gameId 값으로 교차 확인한 값.
TEAM_CODE_MAP = {
    "LG": "LG", "KT": "KT", "SSG": "SK", "NC": "NC",
    "두산": "OB", "KIA": "HT", "삼성": "SS",
    "롯데": "LT", "한화": "HH", "키움": "WO",
}
OUR_TEAM = "삼성 라이온즈"
OUR_TEAM_ABBR = "삼성"

STANDINGS_URL = "https://www.koreabaseball.com/Record/TeamRank/TeamRankDaily.aspx"
SCOREBOARD_URL = "https://www.koreabaseball.com/Schedule/ScoreBoard.aspx"
SCHEDULE_LIST_URL = "https://www.koreabaseball.com/Schedule/Schedule.aspx"
WS_SCHEDULE_LIST_URL = "https://www.koreabaseball.com/ws/Schedule.asmx/GetScheduleList"
WS_SCOREBOARD_URL = "https://www.koreabaseball.com/ws/Schedule.asmx/GetScoreBoardScroll"
WS_BOXSCORE_URL = "https://www.koreabaseball.com/ws/Schedule.asmx/GetBoxScoreScroll"
HITTER_DETAIL_URL = "https://www.koreabaseball.com/Record/Player/HitterDetail/Basic.aspx?playerId={}"
PITCHER_DETAIL_URL = "https://www.koreabaseball.com/Record/Player/PitcherDetail/Basic.aspx?playerId={}"
CONTROLS_SEARCH_URL = "https://www.koreabaseball.com/ws/Controls.asmx/GetSearchPlayer"
POS_NO_TO_GROUP = {"투수": "pitcher", "포수": "catcher", "내야수": "infield", "외야수": "outfield"}
ROSTER_INACTIVE_AFTER_DAYS = 20  # 이 기간 동안 박스스코어에 안 나오면 1군에서 빠진 것으로 간주
LEAGUE_ID = "1"   # 정규시즌
SERIES_ID = "0"


def log(msg):
    print(f"[{datetime.datetime.now().isoformat()}] {msg}", flush=True)


def sb_upsert(table, rows, on_conflict, return_rows=False):
    """Supabase REST API로 upsert. return_rows=True면 저장된 행을 돌려받는다."""
    if not rows:
        return [] if return_rows else None
    url = f"{SUPABASE_URL}/rest/v1/{table}?on_conflict={on_conflict}"
    headers = dict(HEADERS_SB)
    headers["Prefer"] = (
        "resolution=merge-duplicates,return=representation"
        if return_rows else
        "resolution=merge-duplicates,return=minimal"
    )
    r = requests.post(url, headers=headers, data=json.dumps(rows), timeout=20)
    if r.status_code >= 300:
        log(f"  ! {table} upsert 실패 ({r.status_code}): {r.text[:300]}")
        return [] if return_rows else None
    log(f"  - {table} upsert 성공 ({len(rows)}건)")
    return r.json() if return_rows else None


def sb_select(table, query):
    """query 예: 'select=id,name&kbo_player_id=not.is.null'"""
    url = f"{SUPABASE_URL}/rest/v1/{table}?{query}"
    r = requests.get(url, headers=HEADERS_SB, timeout=20)
    if r.status_code >= 300:
        log(f"  ! {table} select 실패 ({r.status_code}): {r.text[:300]}")
        return []
    return r.json()


def sb_delete(table, filter_query):
    """filter_query 예: 'game_date=in.(2026-08-02,2026-08-03)'"""
    url = f"{SUPABASE_URL}/rest/v1/{table}?{filter_query}"
    r = requests.delete(url, headers=HEADERS_SB, timeout=20)
    if r.status_code >= 300:
        log(f"  ! {table} delete 실패 ({r.status_code}): {r.text[:300]}")


def sb_update(table, row_id, fields):
    """PATCH으로 특정 id의 일부 컬럼만 갱신 (upsert와 달리 NOT NULL 컬럼을
    전부 안 보내도 된다)"""
    url = f"{SUPABASE_URL}/rest/v1/{table}?id=eq.{row_id}"
    r = requests.patch(url, headers=HEADERS_SB, data=json.dumps(fields), timeout=20)
    if r.status_code >= 300:
        log(f"  ! {table} update 실패 ({r.status_code}): {r.text[:300]}")


# ---------------------------------------------------------------------------
# 1) 팀 순위
# ---------------------------------------------------------------------------
def crawl_standings():
    log("팀 순위 수집 시작")
    try:
        res = requests.get(STANDINGS_URL, headers=HEADERS_WEB, timeout=15)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, "html.parser")

        # 페이지에 table.tData가 두 개 있음 (팀 순위 표 + 상대전적 표).
        # 순위 표는 #udpRecord의 직계 자식이고, 상대전적 표는 그 안의
        # #pnlVsTeam 안에 중첩되어 있어 직계 자식(>) 선택자로 구분한다.
        table = soup.select_one(
            "#cphContents_cphContents_cphContents_udpRecord > table.tData"
        )
        if not table:
            log("  ! 순위 표를 찾지 못함 (선택자 조정 필요)")
            return

        rows_out = []
        for i, tr in enumerate(table.select("tbody tr"), start=1):
            cells = [td.get_text(strip=True) for td in tr.select("td")]
            if len(cells) < 8:
                continue
            # 열 순서(실제 페이지 확인): 순위,팀명,경기,승,패,무,승률,게임차,최근10경기,연속,홈,방문
            rank, team_raw, games, wins, losses, _draws, win_pct, gb_raw = cells[:8]
            team_full = TEAM_NAME_MAP.get(team_raw, team_raw)
            rows_out.append({
                "team": team_full,
                "is_us": team_full == OUR_TEAM,
                "rank": int(re.sub(r"\D", "", rank) or i),
                "games": int(re.sub(r"\D", "", games) or 0),
                "wins": int(re.sub(r"\D", "", wins) or 0),
                "losses": int(re.sub(r"\D", "", losses) or 0),
                "win_pct": float(win_pct) if win_pct.replace(".", "").isdigit() else 0,
                "games_behind": 0 if gb_raw in ("-", "") else float(gb_raw),
            })

        sb_upsert("standings", rows_out, on_conflict="team")
    except Exception as e:
        log(f"  ! 순위 수집 실패: {e}")


# ---------------------------------------------------------------------------
# KBO 내부 .asmx 웹서비스 호출 헬퍼
# ---------------------------------------------------------------------------
def build_kbo_game_id(game_date_iso, away_abbr, home_abbr):
    away_code = TEAM_CODE_MAP.get(away_abbr)
    home_code = TEAM_CODE_MAP.get(home_abbr)
    if not away_code or not home_code:
        return None
    return f"{game_date_iso.replace('-', '')}{away_code}{home_code}0"


def kbo_ws_post(url, game_id, gyear):
    try:
        headers = dict(HEADERS_WS)
        headers["Referer"] = (
            f"https://www.koreabaseball.com/Schedule/GameCenter/Main.aspx"
            f"?gameDate={game_id[:8]}&gameId={game_id}&section=REVIEW"
        )
        r = requests.post(
            url,
            headers=headers,
            data={"leId": LEAGUE_ID, "srId": SERIES_ID, "seasonId": str(gyear), "gameId": game_id},
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        if str(data.get("code")) != "100":
            log(f"  ! KBO API 응답 code={data.get('code')} msg={data.get('msg')}")
            return None
        return data
    except Exception as e:
        log(f"  ! KBO API 호출 실패 ({url}): {e}")
        return None


def grid_rows(json_str):
    """S2iGridTable 포맷 JSON 문자열 -> [[셀텍스트, ...], ...]"""
    if not json_str:
        return []
    grid = json.loads(json_str)
    return [[cell.get("Text", "") for cell in row["row"]] for row in grid.get("rows", [])]


# ---------------------------------------------------------------------------
# 2-0) 오늘 열리는 다른 팀들 경기 현황 (경쟁팀 파악용) - 삼성 경기 포함 전부 수집
# ---------------------------------------------------------------------------
def crawl_other_games(soup, today_iso):
    rows_out = []
    for block in soup.select(".smsScore"):
        a = block.select_one(".leftTeam .teamT")
        h = block.select_one(".rightTeam .teamT")
        if not a or not h:
            continue
        away_txt, home_txt = a.get_text(strip=True), h.get_text(strip=True)

        state_el = block.select_one(".flag span")
        state_text = state_el.get_text(strip=True) if state_el else ""
        if "취소" in state_text or "노게임" in state_text:
            status = "cancelled"
        elif "종료" in state_text:
            status = "finished"
        elif state_text == "경기전":
            status = "scheduled"
        else:
            status = "live"

        away_score_el = block.select_one(".leftTeam .score span")
        home_score_el = block.select_one(".rightTeam .score span")
        away_score_txt = away_score_el.get_text(strip=True) if away_score_el else ""
        home_score_txt = home_score_el.get_text(strip=True) if home_score_el else ""

        rows_out.append({
            "game_date": today_iso,
            "away_team": away_txt,
            "home_team": home_txt,
            "away_score": int(away_score_txt) if away_score_txt.isdigit() else None,
            "home_score": int(home_score_txt) if home_score_txt.isdigit() else None,
            "status": status,
            "state_text": state_text or None,
        })

    sb_upsert("other_games", rows_out, on_conflict="game_date,away_team,home_team")


# ---------------------------------------------------------------------------
# 2-1) 스코어보드에 삼성 경기가 없을 때 - 월간 일정표(GetScheduleList)의
#      "비고"란에서 우천취소/폭염취소 여부를 확인하는 보조 함수.
#      Schedule.aspx 자체는 빈 뼈대만 서버에서 내려주고 실제 목록은
#      /ws/Schedule.asmx/GetScheduleList 를 AJAX로 호출해서 채운다
#      (표는 항상 "오늘"부터 시작하므로 첫 날짜 그룹만 본다).
# ---------------------------------------------------------------------------
def check_today_cancelled():
    try:
        today = datetime.date.today()
        today_iso = today.isoformat()
        headers = dict(HEADERS_WS)
        headers["Referer"] = SCHEDULE_LIST_URL
        r = requests.post(
            WS_SCHEDULE_LIST_URL,
            headers=headers,
            data={
                "leId": LEAGUE_ID,
                "srIdList": "0,9,6",  # 정규시즌
                "seasonId": str(today.year),
                "gameMonth": f"{today.month:02d}",
                "teamId": "",
            },
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        rows = data.get("rows") or []
        if not rows:
            return None

        # 응답은 이번 달 1일부터 순서대로 오고, "day" 클래스 셀이 있는 행에서만
        # 날짜가 바뀐다 (같은 날 여러 경기는 day 셀 없이 이어짐). rows[0]이
        # 곧 "오늘"이라는 가정은 틀렸다 - 항상 그 달 1일 행이기 때문에, 예전
        # 코드는 매번 1일 경기 정보를 "오늘 경기"인 것처럼 잘못 반환했었다.
        # fetch_month_schedule()과 동일한 방식으로 각 행의 실제 날짜를 추적해서
        # today_iso와 일치하는 행만 골라낸다.
        cur_date = None
        today_rows = []
        for row in rows:
            cells = row["row"]
            day_cell = next((c for c in cells if c.get("Class") == "day"), None)
            if day_cell and day_cell.get("Text"):
                m = re.match(r"(\d{2})\.(\d{2})", day_cell["Text"])
                if m:
                    cur_date = f"{today.year}-{m.group(1)}-{m.group(2)}"
            if cur_date == today_iso:
                today_rows.append(row)
            elif cur_date and cur_date > today_iso:
                break  # 오늘 이후 날짜까지 왔으면 더 볼 필요 없음

        for row in today_rows:
            cells = row["row"]
            play_cell = next((c for c in cells if c.get("Class") == "play"), None)
            if not play_cell or OUR_TEAM_ABBR not in (play_cell.get("Text") or ""):
                continue
            teams = BeautifulSoup(play_cell["Text"], "html.parser").find_all("span", recursive=False)
            if len(teams) < 2:
                continue
            away_raw, home_raw = teams[0].get_text(strip=True), teams[-1].get_text(strip=True)
            remark = (cells[-1].get("Text") or "").strip()
            place = (cells[-2].get("Text") or "").strip() or None
            time_cell = next((c for c in cells if c.get("Class") == "time"), None)
            start_time = (
                BeautifulSoup(time_cell["Text"], "html.parser").get_text(strip=True)
                if time_cell and time_cell.get("Text") else None
            )
            return {
                "away_raw": away_raw, "home_raw": home_raw,
                "remark": remark, "place": place, "start_time": start_time,
            }
        return None
    except Exception as e:
        log(f"  ! 일정표 확인 실패: {e}")
        return None


# ---------------------------------------------------------------------------
# 2-1b) 특정 연/월 전체 일정표에서 삼성 경기만 뽑아내기 (과거 결과 + 다가오는 일정
#       동기화용). GetScheduleList는 조회 월 전체(1일부터)를 돌려준다.
# ---------------------------------------------------------------------------
def fetch_month_schedule(gyear, gmonth):
    try:
        headers = dict(HEADERS_WS)
        headers["Referer"] = SCHEDULE_LIST_URL
        r = requests.post(
            WS_SCHEDULE_LIST_URL,
            headers=headers,
            data={
                "leId": LEAGUE_ID, "srIdList": "0,9,6", "seasonId": str(gyear),
                "gameMonth": f"{gmonth:02d}", "teamId": "",
            },
            timeout=15,
        )
        r.raise_for_status()
        rows = r.json().get("rows") or []
    except Exception as e:
        log(f"  ! {gyear}-{gmonth:02d} 일정표 조회 실패: {e}")
        return []

    out = []
    cur_date = None
    for row in rows:
        cells = row["row"]
        day_cell = next((c for c in cells if c.get("Class") == "day"), None)
        if day_cell and day_cell.get("Text"):
            m = re.match(r"(\d{2})\.(\d{2})", day_cell["Text"])
            if m:
                cur_date = f"{gyear}-{m.group(1)}-{m.group(2)}"
        play_cell = next((c for c in cells if c.get("Class") == "play"), None)
        if not play_cell or not cur_date:
            continue
        text = play_cell.get("Text") or ""
        if OUR_TEAM_ABBR not in text:
            continue
        teams = BeautifulSoup(text, "html.parser").find_all("span", recursive=False)
        if len(teams) < 2:
            continue
        away_raw, home_raw = teams[0].get_text(strip=True), teams[-1].get_text(strip=True)
        remark = (cells[-1].get("Text") or "").strip()
        place = (cells[-2].get("Text") or "").strip() or None
        time_cell = next((c for c in cells if c.get("Class") == "time"), None)
        start_time = (
            BeautifulSoup(time_cell["Text"], "html.parser").get_text(strip=True)
            if time_cell and time_cell.get("Text") else None
        )
        out.append({
            "game_date": cur_date, "away_raw": away_raw, "home_raw": home_raw,
            "remark": remark, "place": place, "start_time": start_time,
        })
    return out


def crawl_full_schedule():
    """최근 지난 경기 결과 + 다가오는 일정을 실제 KBO 일정표 기준으로 통째로 다시 맞춘다.
    (하루 2번 daily-refresh에서만 호출 - 무거운 작업이라 5분 폴링에서는 돌리지 않는다)
    """
    log("전체 일정 동기화 시작 (최근 결과 + 다가오는 일정)")
    today = datetime.date.today()
    today_iso = today.isoformat()

    months = {(today.year, today.month)}
    next_month_probe = today.replace(day=28) + datetime.timedelta(days=4)
    months.add((next_month_probe.year, next_month_probe.month))
    prev_month_probe = today.replace(day=1) - datetime.timedelta(days=1)
    months.add((prev_month_probe.year, prev_month_probe.month))

    all_games = []
    for gyear, gmonth in months:
        all_games.extend(fetch_month_schedule(gyear, gmonth))

    # 지난 결과 재확인 범위: 7일이면 한 번 수집 실패한 경기가 창 밖으로
    # 밀려나 영원히 "예정" 상태로 남는 문제가 있어 30일로 넓혔다.
    window_start = (today - datetime.timedelta(days=30)).isoformat()
    by_date = {
        g["game_date"]: g for g in all_games
        if window_start <= g["game_date"] and g["game_date"] != today_iso
    }
    if not by_date:
        log("  - 동기화할 경기 없음")
        return

    rows_out = []
    for game_date, g in sorted(by_date.items()):
        is_home = g["home_raw"] == OUR_TEAM_ABBR
        opponent_raw = g["away_raw"] if is_home else g["home_raw"]
        opponent = TEAM_NAME_MAP.get(opponent_raw, opponent_raw)
        remark = g["remark"]

        row = {
            "game_date": game_date, "opponent": opponent, "is_home": is_home,
            "start_time": g["start_time"], "place": g["place"],
        }

        # KBO 비고란 문구가 정확히 "취소"/"노게임"이 아닌 경우(예: "그라운드사정",
        # "우천순연", "미세먼지" 등)도 놓치지 않도록 키워드를 넓혀서 판정한다.
        CANCEL_KEYWORDS = ("취소", "노게임", "순연", "우천", "폭염", "미세먼지", "황사", "그라운드")
        if any(kw in remark for kw in CANCEL_KEYWORDS):
            row.update({"status": "cancelled", "cancel_reason": remark,
                        "result": None, "score_us": None, "score_opp": None})
        elif game_date < today_iso:
            kbo_game_id = build_kbo_game_id(game_date, g["away_raw"], g["home_raw"])
            row["kbo_game_id"] = kbo_game_id
            sb_data = kbo_ws_post(WS_SCOREBOARD_URL, kbo_game_id, today.year) if kbo_game_id else None

            score_us = score_opp = None
            if sb_data:
                table2 = grid_rows(sb_data.get("table2"))
                table3 = grid_rows(sb_data.get("table3"))

                def as_int(v):
                    try:
                        return int(v)
                    except (TypeError, ValueError):
                        return None

                if len(table3) >= 2 and all(len(r) >= 4 for r in table3[:2]):
                    away_rhe, home_rhe = table3[0], table3[1]
                    away_score, home_score = as_int(away_rhe[0]), as_int(home_rhe[0])
                    if away_score is not None and home_score is not None:
                        score_us = home_score if is_home else away_score
                        score_opp = away_score if is_home else home_score
                    us_rhe, opp_rhe = (home_rhe, away_rhe) if is_home else (away_rhe, home_rhe)
                    row["hits_us"], row["errors_us"], row["walks_us"] = (
                        as_int(us_rhe[1]), as_int(us_rhe[2]), as_int(us_rhe[3])
                    )
                    row["hits_opp"], row["errors_opp"], row["walks_opp"] = (
                        as_int(opp_rhe[1]), as_int(opp_rhe[2]), as_int(opp_rhe[3])
                    )
                if len(table2) >= 2:
                    away_line = [c if c != "-" else None for c in table2[0]]
                    home_line = [c if c != "-" else None for c in table2[1]]
                    row["linescore_us"] = home_line if is_home else away_line
                    row["linescore_opp"] = away_line if is_home else home_line
                row["crowd_count"] = sb_data.get("CROWD_CN") or None
                row["game_duration"] = sb_data.get("USE_TM") or None

            status = "finished" if score_us is not None else "scheduled"
            result = None
            if status == "finished":
                result = "win" if score_us > score_opp else ("loss" if score_us < score_opp else "draw")
            elif remark:
                # 확장한 키워드로도 못 잡은 취소/순연 표현에 대한 안전장치.
                # 과거 날짜인데 점수가 없고 비고란에 뭔가 문구가 남아있다면
                # "예정"으로 방치하지 말고 그 문구를 취소 사유로 그대로 기록한다.
                status = "cancelled"
                row["cancel_reason"] = remark
                log(f"  - {game_date} vs {opponent} 과거 경기, 점수 없음 + 비고 '{remark}' "
                    f"-> 취소로 처리 (키워드 목록에 없는 표현일 수 있음, 확인 권장)")
            else:
                # 과거 경기인데 점수도 없고 비고도 없는 경우 - 원인 파악용 진단 로그.
                # (sb_data가 None이면 kbo_ws_post 자체가 실패한 것 - 그쪽에서
                #  이미 로그를 남긴다. sb_data는 있는데 여기로 온 거면 응답
                #  구조가 예상과 달랐다는 뜻이라 원본을 일부 남겨 확인한다.)
                if sb_data:
                    log(f"  ! {game_date} vs {opponent} 점수 파싱 실패 "
                        f"(kbo_game_id={kbo_game_id}, table3 원본 일부: "
                        f"{json.dumps(sb_data.get('table3'))[:200]})")
                else:
                    log(f"  ! {game_date} vs {opponent} 과거 경기 점수 조회 실패 "
                        f"(kbo_game_id={kbo_game_id})")
            row.update({"status": status, "result": result, "score_us": score_us, "score_opp": score_opp})
        else:
            row.update({"status": "scheduled", "result": None, "score_us": None, "score_opp": None})

        rows_out.append(row)

    # PostgREST(Supabase)는 upsert 배열의 모든 객체가 동일한 키 집합을
    # 가져야 한다 (하나라도 다르면 PGRST102 "All object keys must match"
    # 에러로 배치 전체가 거부된다). cancelled/finished/scheduled 상태마다
    # 채워지는 필드가 달라서, 여기서 전체 컬럼의 합집합을 구해 없는 값은
    # None으로 채워 모든 행의 키를 통일시킨다.
    all_keys = set()
    for row in rows_out:
        all_keys.update(row.keys())
    rows_out = [{k: row.get(k) for k in all_keys} for row in rows_out]

    # 스캔한 날짜 구간 전체(경기가 없는 휴식일 포함)의 기존 행을 지우고 새로 채워 넣는다.
    # 매치된 날짜만 지우면, 실제로는 경기가 없어졌는데 예전에 잘못 저장된 행이
    # 계속 남아있는 문제가 생긴다 (예: 원래 있던 mock 데이터의 휴식일 오류).
    range_start = min(by_date.keys())
    range_end = max(by_date.keys())
    sb_delete(
        "games",
        f"game_date=gte.{range_start}&game_date=lte.{range_end}&game_date=neq.{today_iso}",
    )

    saved = sb_upsert("games", rows_out, on_conflict="game_date,opponent", return_rows=True)

    if saved:
        saved_by_date = {row["game_date"]: row for row in saved}
        for row in rows_out:
            if row.get("status") == "finished" and row.get("kbo_game_id"):
                saved_row = saved_by_date.get(row["game_date"])
                if saved_row:
                    crawl_boxscore(row["kbo_game_id"], today.year, saved_row["id"], row.get("is_home"))


# ---------------------------------------------------------------------------
# 2-2) 경기 일정 / 결과 (삼성 경기, 오늘자) + 이닝별 스코어보드 + 실시간 진행상황
# ---------------------------------------------------------------------------
def crawl_schedule_and_results():
    log("일정/결과 수집 시작")
    try:
        today = datetime.date.today()
        res = requests.get(SCOREBOARD_URL, headers=HEADERS_WEB, timeout=15)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, "html.parser")

        crawl_other_games(soup, today.isoformat())

        target = away_raw = home_raw = None
        for block in soup.select(".smsScore"):
            a = block.select_one(".leftTeam .teamT")
            h = block.select_one(".rightTeam .teamT")
            if not a or not h:
                continue
            a_txt, h_txt = a.get_text(strip=True), h.get_text(strip=True)
            if OUR_TEAM_ABBR in (a_txt, h_txt):
                target, away_raw, home_raw = block, a_txt, h_txt
                break

        if not target:
            # 스코어보드에 없으면 우천/폭염취소이거나 애초에 오늘 경기가 없는 것.
            # 월간 일정표의 "비고"란으로 취소 여부를 한 번 더 확인한다.
            info = check_today_cancelled()
            if not info:
                log("  - 오늘 삼성 경기 없음")
                return
            if "취소" not in info["remark"] and "노게임" not in info["remark"]:
                log(f"  - 오늘 삼성 경기 없음 (일정표 비고: {info['remark'] or '-'})")
                return

            is_home = info["home_raw"] == OUR_TEAM_ABBR
            opponent_raw = info["away_raw"] if is_home else info["home_raw"]
            opponent = TEAM_NAME_MAP.get(opponent_raw, opponent_raw)
            row = {
                "game_date": today.isoformat(),
                "opponent": opponent,
                "is_home": is_home,
                "start_time": info["start_time"],
                "place": info["place"],
                "status": "cancelled",
                "cancel_reason": info["remark"],
                "result": None,
                "score_us": None,
                "score_opp": None,
            }
            sb_upsert("games", [row], on_conflict="game_date,opponent")
            log(f"  - 오늘 삼성 경기 취소 확인 ({info['remark']}) - vs {opponent}")
            return

        is_home = home_raw == OUR_TEAM_ABBR
        opponent_raw = away_raw if is_home else home_raw
        opponent = TEAM_NAME_MAP.get(opponent_raw, opponent_raw)

        state_el = target.select_one(".flag span")
        state_text = state_el.get_text(strip=True) if state_el else ""
        inning_match = re.match(r"^(\d+)회(초|말)$", state_text)
        if "취소" in state_text or "노게임" in state_text:
            status = "cancelled"
        elif "종료" in state_text:
            status = "finished"
        elif state_text == "경기전":
            status = "scheduled"
        else:
            status = "live"  # "n회초/말" 등 진행 중 표시 전부 포함

        current_inning = int(inning_match.group(1)) if inning_match else None
        current_half = {"초": "top", "말": "bottom"}[inning_match.group(2)] if inning_match else None

        away_score_el = target.select_one(".leftTeam .score span")
        home_score_el = target.select_one(".rightTeam .score span")
        away_score_txt = away_score_el.get_text(strip=True) if away_score_el else ""
        home_score_txt = home_score_el.get_text(strip=True) if home_score_el else ""

        score_us = score_opp = None
        result = None
        if away_score_txt.isdigit() and home_score_txt.isdigit():
            away_score, home_score = int(away_score_txt), int(home_score_txt)
            score_us = home_score if is_home else away_score
            score_opp = away_score if is_home else home_score
            if status == "finished":
                result = "win" if score_us > score_opp else ("loss" if score_us < score_opp else "draw")

        # 실시간 진행상황(주자/아웃) - 경기 중일 때만 채움. 빈 베이스 이미지가
        # ".../common/base.png" 라는 걸 확인했고, 주자가 있으면 다른 이미지로 바뀔
        # 것이라는 전제로 판단한다 (실제 라이브 경기로는 아직 검증하지 못함).
        outs = base1 = base2 = base3 = None
        base_div = target.select_one(".base")
        if base_div is not None and status == "live":
            out_text = base_div.get_text(" ", strip=True)
            m = re.search(r"(\d+)\s*out", out_text, re.IGNORECASE)
            outs = int(m.group(1)) if m else 0

            def occupied(cls):
                img = base_div.select_one(f".{cls} img")
                src = img.get("src") if img else None
                if not src:
                    return False
                return not src.rstrip("/").endswith("/common/base.png")

            base1, base2, base3 = occupied("base1"), occupied("base2"), occupied("base3")

        place_el = target.select_one(".place")
        place_full = place_el.get_text(" ", strip=True) if place_el else ""
        start_time_el = target.select_one(".place span")
        start_time = start_time_el.get_text(strip=True) if start_time_el else None
        place_name = re.sub(r"\s*\d{1,2}:\d{2}\s*$", "", place_full).strip() or None

        win_pitcher = save_pitcher = lose_pitcher = None
        win_p = target.select_one(".win")
        if win_p:
            for s in win_p.select("span"):
                text = s.get_text(strip=True)
                if text.startswith("승:"):
                    win_pitcher = text[2:].strip()
                elif text.startswith("세:"):
                    save_pitcher = text[2:].strip()
                elif text.startswith("패:"):
                    lose_pitcher = text[2:].strip()

        row = {
            "game_date": today.isoformat(),
            "opponent": opponent,
            "is_home": is_home,
            "start_time": start_time,
            "place": place_name,
            "status": status,
            "cancel_reason": state_text if status == "cancelled" else None,
            "result": result,
            "score_us": score_us,
            "score_opp": score_opp,
            "current_inning": current_inning,
            "current_half": current_half,
            "outs": outs,
            "base1": base1,
            "base2": base2,
            "base3": base3,
            "win_pitcher": win_pitcher,
            "save_pitcher": save_pitcher,
            "lose_pitcher": lose_pitcher,
        }

        kbo_game_id = build_kbo_game_id(today.isoformat(), away_raw, home_raw)
        row["kbo_game_id"] = kbo_game_id

        # 이닝별 스코어보드 (경기 중/종료일 때만 의미 있음)
        if kbo_game_id and status in ("live", "finished"):
            sb_data = kbo_ws_post(WS_SCOREBOARD_URL, kbo_game_id, today.year)
            if sb_data:
                table2 = grid_rows(sb_data.get("table2"))  # 이닝별 점수 [away, home]
                table3 = grid_rows(sb_data.get("table3"))  # [R,H,E,B] [away, home]

                if len(table2) >= 2:
                    away_line = [c if c != "-" else None for c in table2[0]]
                    home_line = [c if c != "-" else None for c in table2[1]]
                    row["linescore_us"] = home_line if is_home else away_line
                    row["linescore_opp"] = away_line if is_home else home_line

                def as_int(v):
                    try:
                        return int(v)
                    except (TypeError, ValueError):
                        return None

                if len(table3) >= 2 and all(len(r) >= 4 for r in table3[:2]):
                    away_rhe, home_rhe = table3[0], table3[1]
                    us_rhe, opp_rhe = (home_rhe, away_rhe) if is_home else (away_rhe, home_rhe)
                    row["hits_us"], row["errors_us"], row["walks_us"] = (
                        as_int(us_rhe[1]), as_int(us_rhe[2]), as_int(us_rhe[3])
                    )
                    row["hits_opp"], row["errors_opp"], row["walks_opp"] = (
                        as_int(opp_rhe[1]), as_int(opp_rhe[2]), as_int(opp_rhe[3])
                    )

                row["crowd_count"] = sb_data.get("CROWD_CN") or None
                row["game_duration"] = sb_data.get("USE_TM") or None
                if not row["start_time"] and sb_data.get("START_TM"):
                    row["start_time"] = sb_data.get("START_TM")

        saved = sb_upsert("games", [row], on_conflict="game_date,opponent", return_rows=True)
        game_row_id = saved[0]["id"] if saved else None

        if game_row_id and status in ("live", "finished") and kbo_game_id:
            crawl_boxscore(kbo_game_id, today.year, game_row_id, is_home, finished=(status == "finished"))

    except Exception as e:
        log(f"  ! 일정/결과 수집 실패: {e}")


# ---------------------------------------------------------------------------
# 2-3) 선수단 자동 동기화 - 박스스코어에 새 이름이 나타나면(콜업) 즉시 등록하고,
#      한동안 안 나타나면(말소) 비활성 처리해 로스터를 최신 상태로 유지한다.
#      한 번 등록해두고 방치되던 방식 대신, 실제 출전 기록을 근거로 자동 반영한다.
# ---------------------------------------------------------------------------
def search_kbo_player(name, team_code=None):
    """이름으로 KBO 전체 선수 검색 (콜업 등으로 처음 보는 선수의 ID/포지션을 즉시 찾기 위함).
    동명이인은 team_code로 걸러낸다 (예: 삼성 소속만)."""
    try:
        headers = dict(HEADERS_WS)
        headers["Referer"] = "https://www.koreabaseball.com/Player/Search.aspx"
        r = requests.post(CONTROLS_SEARCH_URL, headers=headers, data={"name": name}, timeout=15)
        r.raise_for_status()
        data = r.json()
        if str(data.get("code")) != "100":
            return None
        candidates = data.get("now") or []
        if team_code:
            candidates = [c for c in candidates if c.get("T_ID") == team_code]
        return candidates[0] if candidates else None
    except Exception as e:
        log(f"  ! KBO 선수 검색 실패 ({name}): {e}")
        return None


def fetch_player_profile(kbo_player_id, is_pitcher):
    url = (PITCHER_DETAIL_URL if is_pitcher else HITTER_DETAIL_URL).format(kbo_player_id)
    res = requests.get(url, headers=HEADERS_WEB, timeout=15)
    res.raise_for_status()
    soup = BeautifulSoup(res.text, "html.parser")
    prefix = "cphContents_cphContents_cphContents_playerProfile_"

    def txt(suffix):
        el = soup.select_one(f"#{prefix}{suffix}")
        return el.get_text(strip=True) if el else None

    birth_date = None
    birthday_raw = txt("lblBirthday")  # "1993년 02월 12일"
    if birthday_raw:
        m = re.match(r"(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일", birthday_raw)
        if m:
            birth_date = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"

    photo_url = None
    img = soup.select_one(f"#{prefix}imgProgile")
    if img and img.get("src"):
        src = img["src"]
        photo_url = ("https:" + src) if src.startswith("//") else src

    return {
        "birth_date": birth_date,
        "career": txt("lblCareer"),
        "salary_display": txt("lblSalary"),
        "photo_url": photo_url,
    }


_known_player_cache = {}  # 같은 실행 안에서 같은 이름을 중복 조회하지 않기 위한 캐시


def ensure_player_registered(player_name, game_date):
    if player_name in _known_player_cache:
        return
    _known_player_cache[player_name] = True

    existing = sb_select("players", f"select=id,is_active&name=eq.{requests.utils.quote(player_name)}")
    if existing:
        p = existing[0]
        fields = {"last_played_date": game_date}
        if not p.get("is_active"):
            fields["is_active"] = True
            log(f"  + {player_name} 1군 재등록 확인 (최근 출전)")
        sb_update("players", p["id"], fields)
        return

    found = search_kbo_player(player_name, team_code=TEAM_CODE_MAP[OUR_TEAM_ABBR])
    if not found:
        sb_upsert("players", [{
            "name": player_name, "position_group": "infield",
            "is_active": True, "last_played_date": game_date,
        }], on_conflict="name")
        log(f"  + 신규 선수 '{player_name}' 등록 (KBO 검색 실패 - 기본 정보만)")
        return

    is_pitcher = "PitcherDetail" in (found.get("P_LINK") or "")
    row = {
        "name": player_name,
        "back_number": int(found["BACK_NO"]) if str(found.get("BACK_NO")).isdigit() else None,
        "position_group": POS_NO_TO_GROUP.get(found.get("POS_NO"), "infield"),
        "throws_bats": found.get("P_TYPE"),
        "kbo_player_id": str(found.get("P_ID")),
        "is_active": True,
        "last_played_date": game_date,
    }
    try:
        profile = fetch_player_profile(found["P_ID"], is_pitcher)
        row.update({k: v for k, v in profile.items() if v})
    except Exception as e:
        log(f"  ! {player_name} 프로필 조회 실패: {e}")

    sb_upsert("players", [row], on_conflict="name")
    log(f"  + 신규 선수(콜업) 자동 등록: {player_name} #{row.get('back_number')}")


def deactivate_stale_players():
    """최근 N일간 박스스코어에 한 번도 안 나온 선수는 1군에서 빠진 것으로 보고 비활성 처리."""
    cutoff = (datetime.date.today() - datetime.timedelta(days=ROSTER_INACTIVE_AFTER_DAYS)).isoformat()
    stale = sb_select(
        "players",
        f"select=id,name&is_active=eq.true&or=(last_played_date.lt.{cutoff},last_played_date.is.null)",
    )
    for p in stale:
        sb_update("players", p["id"], {"is_active": False})
    if stale:
        log(f"  - 선수단 비활성 처리 {len(stale)}명 (최근 {ROSTER_INACTIVE_AFTER_DAYS}일간 출전 없음): "
            + ", ".join(p["name"] for p in stale))


# ---------------------------------------------------------------------------
# 3) 경기 종료 후 선수별 기록(박스스코어) 자동 수집 - 삼성 선수만
# ---------------------------------------------------------------------------
def crawl_boxscore(kbo_game_id, gyear, game_row_id, is_home=None, finished=True):
    log("  선수별 기록(박스스코어) 수집 시작" + ("" if finished else " (경기 진행 중 - 실시간)"))
    data = kbo_ws_post(WS_BOXSCORE_URL, kbo_game_id, gyear)
    if not data:
        log("  ! 박스스코어 조회 실패")
        return

    game_date = f"{kbo_game_id[:4]}-{kbo_game_id[4:6]}-{kbo_game_id[6:8]}"
    rows_out = []
    sort_order = 1

    # arrHitter/arrPitcher는 [원정팀, 홈팀] 순서로 온다. is_home을 모르면(과거 호출
    # 호환용) 상대팀까지 포함해 기존처럼 전부 저장한다.
    hitter_sides = data.get("arrHitter", []) or []
    pitcher_sides = data.get("arrPitcher", []) or []
    if is_home is not None:
        idx = 1 if is_home else 0
        hitter_sides = hitter_sides[idx:idx + 1]
        pitcher_sides = pitcher_sides[idx:idx + 1]

    for side in hitter_sides:
        names = grid_rows(side.get("table1"))   # [타순, 포지션, 이름]
        stats = grid_rows(side.get("table3"))   # [타수, 안타, 타점, 득점, 타율]
        for name_row, stat_row in zip(names, stats):
            if len(name_row) < 3 or len(stat_row) < 4:
                continue
            player_name = name_row[2].strip()
            position = name_row[1].strip()
            if not player_name:
                continue
            ensure_player_registered(player_name, game_date)
            ab, h, rbi, r = stat_row[0], stat_row[1], stat_row[2], stat_row[3]
            rows_out.append({
                "game_id": game_row_id,
                "player_name": player_name,
                "position": position,
                "stat_line": f"{ab}타수 {h}안타 {rbi}타점 {r}득점",
                "stat_type": "batting",
                "sort_order": sort_order,
            })
            sort_order += 1

    for side in pitcher_sides:
        rows = grid_rows(side.get("table"))  # 선수명,등판,결과,승,패,세,이닝,타자,투구수,타수,피안타,홈런,4사구,삼진,실점,자책,평균자책점
        for r in rows:
            if len(r) < 16:
                continue
            name, appearance, decision = r[0].strip(), r[1].strip(), r[2].strip()
            innings, so, er = r[6], r[13], r[15]
            if not name:
                continue
            ensure_player_registered(name, game_date)
            if decision and decision not in ("&nbsp;", ""):
                label = decision
            elif appearance == "선발":
                label = "선발투수"
            else:
                label = "구원투수"
            rows_out.append({
                "game_id": game_row_id,
                "player_name": name,
                "position": label,
                "stat_line": f"{innings}이닝 {er}자책 {so}탈삼진",
                "stat_type": "pitching",
                "sort_order": sort_order,
            })
            sort_order += 1

    sb_upsert("game_player_stats", rows_out, on_conflict="game_id,sort_order")

    # 결승타는 경기가 끝나야 확정되는 값이라, 진행 중인 경기에서 잘못된(또는 아직
    # 비어있는) 값을 잘못 반영하지 않도록 경기 종료 때만 기록한다.
    if finished:
        etc_rows = grid_rows(data.get("tableEtc"))
        highlight = None
        for r in etc_rows:
            if len(r) >= 2 and "결승타" in (r[0] or ""):
                highlight = r[1].strip()
                break
        if highlight:
            sb_update("games", game_row_id, {"highlight_note": highlight})


# ---------------------------------------------------------------------------
# 4) 선수별 시즌 기록 (KBO 기록실 개인 상세 페이지) - 하루 1~2번만 갱신
# ---------------------------------------------------------------------------
def parse_first_data_row(table):
    """KBO 기록실 tbl-type02 표의 첫 tbody tr 셀 텍스트 리스트를 반환 (없으면 None)"""
    if not table:
        return None
    tr = table.select_one("tbody tr")
    if not tr:
        return None
    cells = [td.get_text(strip=True) for td in tr.select("td")]
    return cells or None


def fetch_player_season_stat(kbo_player_id, is_pitcher):
    url = (PITCHER_DETAIL_URL if is_pitcher else HITTER_DETAIL_URL).format(kbo_player_id)
    res = requests.get(url, headers=HEADERS_WEB, timeout=15)
    res.raise_for_status()
    soup = BeautifulSoup(res.text, "html.parser")

    # 선수 상세 페이지의 "OOOO 성적" 표 2개(기본기록/추가기록)는 div.player_records
    # 안에서 "최근 10경기" 표들보다 앞에 나오는 첫 두 개의 table.tbl.tt다.
    tables = soup.select("div.player_records table.tbl.tt")
    if len(tables) < 2:
        return None
    main_cells = parse_first_data_row(tables[0])
    extra_cells = parse_first_data_row(tables[1])
    if not main_cells:
        return None

    if is_pitcher:
        # 팀명,ERA,G,CG,SHO,W,L,SV,HLD,WPCT,TBF,NP,IP,H,2B,3B,HR
        if len(main_cells) < 9:
            return None
        era, g, _cg, _sho, w, l, sv, hld = main_cells[1:9]
        return f"{g}경기 {w}승 {l}패 {sv}세이브 {hld}홀드 · 평균자책점 {era}"

    # 팀명,AVG,G,PA,AB,R,H,2B,3B,HR,TB,RBI,SB,CS,SAC,SF
    if len(main_cells) < 12:
        return None
    avg, g, _pa, _ab, r, h, _2b, _3b, hr, _tb, rbi = main_cells[1:12]
    ops = extra_cells[10] if extra_cells and len(extra_cells) > 10 else None
    summary = f"{g}경기 타율 {avg} {hr}홈런 {rbi}타점 {r}득점 {h}안타"
    if ops:
        summary += f" · OPS {ops}"
    return summary


def crawl_season_stats():
    log("선수 시즌 기록 수집 시작")
    players = sb_select("players", "select=id,name,position_group,kbo_player_id&kbo_player_id=not.is.null")
    if not players:
        log("  - kbo_player_id가 등록된 선수 없음")
        return

    updated = 0
    for p in players:
        try:
            summary = fetch_player_season_stat(p["kbo_player_id"], p.get("position_group") == "pitcher")
            if summary:
                sb_update("players", p["id"], {"season_stat_summary": summary})
                updated += 1
        except Exception as e:
            log(f"  ! {p.get('name')} 시즌 기록 조회 실패: {e}")
        time.sleep(0.3)  # KBO 서버에 과도한 연속 요청을 피하기 위한 최소 간격

    log(f"  - 선수 시즌 기록 {updated}/{len(players)}명 갱신 완료")


# ---------------------------------------------------------------------------
# 5) 삼성 제외 전체 팀 경기 일정/결과 (일정 상세의 "같은 날 다른 경기"용)
#    GetScheduleList의 play 셀은 경기가 끝난 카드면 <em><span class="win/lose/same">
#    형태로 스코어까지 이미 포함해서 내려주므로, 팀별 스코어보드를 따로 호출할
#    필요 없이 이 한 번의 월간 일정 조회로 지난 결과 + 다가오는 일정을 모두 얻는다.
# ---------------------------------------------------------------------------
def parse_play_cell(html_text):
    if not html_text:
        return None
    frag = BeautifulSoup(html_text, "html.parser")
    team_spans = frag.find_all("span", recursive=False)
    if len(team_spans) < 2:
        return None
    away_raw, home_raw = team_spans[0].get_text(strip=True), team_spans[-1].get_text(strip=True)

    away_score = home_score = None
    em = frag.find("em")
    if em:
        score_spans = em.find_all("span")
        if len(score_spans) >= 2:
            a_txt, h_txt = score_spans[0].get_text(strip=True), score_spans[-1].get_text(strip=True)
            away_score = int(a_txt) if a_txt.isdigit() else None
            home_score = int(h_txt) if h_txt.isdigit() else None

    return {"away_raw": away_raw, "home_raw": home_raw, "away_score": away_score, "home_score": home_score}


def fetch_month_schedule_all(gyear, gmonth):
    """해당 연/월의 전체 팀 경기(삼성 포함)를 그대로 반환. crawl_full_schedule()의
    fetch_month_schedule()과 같은 GetScheduleList 응답을 재사용하되, 팀 필터링 없이
    스코어까지 함께 파싱한다."""
    try:
        headers = dict(HEADERS_WS)
        headers["Referer"] = SCHEDULE_LIST_URL
        r = requests.post(
            WS_SCHEDULE_LIST_URL,
            headers=headers,
            data={
                "leId": LEAGUE_ID, "srIdList": "0,9,6", "seasonId": str(gyear),
                "gameMonth": f"{gmonth:02d}", "teamId": "",
            },
            timeout=15,
        )
        r.raise_for_status()
        rows = r.json().get("rows") or []
    except Exception as e:
        log(f"  ! {gyear}-{gmonth:02d} 전체 일정표 조회 실패: {e}")
        return []

    out = []
    cur_date = None
    for row in rows:
        cells = row["row"]
        day_cell = next((c for c in cells if c.get("Class") == "day"), None)
        if day_cell and day_cell.get("Text"):
            m = re.match(r"(\d{2})\.(\d{2})", day_cell["Text"])
            if m:
                cur_date = f"{gyear}-{m.group(1)}-{m.group(2)}"
        play_cell = next((c for c in cells if c.get("Class") == "play"), None)
        if not play_cell or not cur_date:
            continue
        parsed = parse_play_cell(play_cell.get("Text"))
        if not parsed:
            continue
        remark = (cells[-1].get("Text") or "").strip()
        time_cell = next((c for c in cells if c.get("Class") == "time"), None)
        start_time = (
            BeautifulSoup(time_cell["Text"], "html.parser").get_text(strip=True)
            if time_cell and time_cell.get("Text") else None
        )
        out.append({"game_date": cur_date, "remark": remark, "start_time": start_time, **parsed})
    return out


def crawl_full_other_games():
    """삼성 경기는 games 테이블에서 이미 상세히 다루므로, 여기서는 나머지 팀들의
    경기만 지난 결과(과거)/다가오는 일정(미래) 구간으로 other_games에 채워 넣는다."""
    log("전체 팀(삼성 제외) 일정/결과 동기화 시작")
    today = datetime.date.today()
    today_iso = today.isoformat()

    months = {(today.year, today.month)}
    next_month_probe = today.replace(day=28) + datetime.timedelta(days=4)
    months.add((next_month_probe.year, next_month_probe.month))
    prev_month_probe = today.replace(day=1) - datetime.timedelta(days=1)
    months.add((prev_month_probe.year, prev_month_probe.month))

    all_games = []
    for gyear, gmonth in months:
        all_games.extend(fetch_month_schedule_all(gyear, gmonth))

    window_start = (today - datetime.timedelta(days=14)).isoformat()
    window_end = (today + datetime.timedelta(days=30)).isoformat()
    filtered = [
        g for g in all_games
        if window_start <= g["game_date"] <= window_end
        and g["game_date"] != today_iso
        and OUR_TEAM_ABBR not in (g["away_raw"], g["home_raw"])
    ]
    if not filtered:
        log("  - 동기화할 타팀 경기 없음")
        return

    rows_out = []
    for g in filtered:
        away_team = TEAM_NAME_MAP.get(g["away_raw"], g["away_raw"])
        home_team = TEAM_NAME_MAP.get(g["home_raw"], g["home_raw"])
        remark = g["remark"]
        if any(kw in remark for kw in ("취소", "노게임", "순연", "우천", "폭염", "미세먼지", "황사", "그라운드")):
            status, state_text = "cancelled", remark
        elif g["away_score"] is not None and g["home_score"] is not None:
            status, state_text = "finished", "종료"
        elif g["game_date"] < today_iso:
            status, state_text = "finished", "종료"
        else:
            status, state_text = "scheduled", (g["start_time"] or "경기 예정")
        rows_out.append({
            "game_date": g["game_date"], "away_team": away_team, "home_team": home_team,
            "away_score": g["away_score"], "home_score": g["home_score"],
            "status": status, "state_text": state_text,
        })

    range_start = min(g["game_date"] for g in filtered)
    range_end = max(g["game_date"] for g in filtered)
    sb_delete(
        "other_games",
        f"game_date=gte.{range_start}&game_date=lte.{range_end}&game_date=neq.{today_iso}",
    )
    sb_upsert("other_games", rows_out, on_conflict="game_date,away_team,home_team")


def main():
    crawl_standings()
    crawl_schedule_and_results()
    # 월 전체 일정표를 다시 긁어오는 건 API 호출이 많아 무거우니, 5분마다 도는
    # 라이브 폴링이 아니라 하루 2번 daily-refresh에서만 돌린다 (FULL_SYNC=1).
    if os.environ.get("FULL_SYNC") == "1":
        crawl_full_schedule()
        crawl_full_other_games()
        crawl_season_stats()
        deactivate_stale_players()
    log("완료")


if __name__ == "__main__":
    main()
