(function () {
  "use strict";

  var cfg = window.LIONS_CONFIG;
  var sb = window.supabase.createClient(cfg.SUPABASE_URL, cfg.SUPABASE_ANON_KEY);

  var POSITION_LABELS = { pitcher: "투수", catcher: "포수", infield: "내야수", outfield: "외야수" };
  var POSITION_ORDER = ["pitcher", "catcher", "infield", "outfield"];
  var RESULT_LABEL = { win: "승", loss: "패", draw: "무" };
  var STATUS_LABEL = { scheduled: "경기 예정", live: "경기 중", finished: "경기 종료", cancelled: "경기 취소" };
  var TICKET_LABEL = { open: "예매중", upcoming: "예정", closed: "마감" };
  var TITLES = { home: "홈", schedule: "일정", standings: "순위", roster: "선수단", game: "경기 상세", player: "선수 프로필" };
  var NAV_MAP = { home: "home", schedule: "schedule", standings: "standings", roster: "roster", game: "schedule", player: "roster" };

  // TVING은 2025시즌부터 KBO 리그 온라인/모바일 독점 중계권을 보유하고 있어,
  // 공식 KBO 허브 페이지(tving.com/sports/kbo)로 연결합니다. 앱이 설치돼 있으면
  // 앱 링크(App Links/Universal Links)로 앱이 열리고, 없으면 웹에서 바로 시청할 수 있습니다.
  var TVING_URL = "https://www.tving.com/sports/kbo";

  var DATA = { standings: [], games: [], tickets: [], players: [], stats: [], otherGames: [] };
  var gamesById = {};
  var statsByGame = {};
  var playersById = {};
  var playersByName = {};

  var calState = null; // {year, month(0-11)} set on first schedule render

  // ---------------------------------------------------------------- utils
  function todayStr() {
    var d = new Date();
    return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate());
  }
  function pad(n) { return String(n).padStart(2, "0"); }
  function fmtDate(iso) {
    var d = new Date(iso + "T00:00:00");
    var days = ["일", "월", "화", "수", "목", "금", "토"];
    return (d.getMonth() + 1) + "." + d.getDate() + "(" + days[d.getDay()] + ")";
  }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  // ---------------------------------------------------------------- router
  function parseHash() {
    var h = (location.hash || "#/home").replace(/^#\//, "");
    var parts = h.split("/");
    return { view: parts[0] || "home", id: parts[1] };
  }

  function renderRoute() {
    var route = parseHash();
    if (!TITLES[route.view]) route = { view: "home" };

    document.querySelectorAll(".view").forEach(function (el) {
      el.classList.toggle("active", el.dataset.view === route.view);
    });
    // 상단 타이틀은 "균이네 사자가족" 고정 (현재 위치는 아래 탭바가 보여줌)
    document.getElementById("backBtn").hidden = !(route.view === "game" || route.view === "player");

    document.querySelectorAll(".tabnav a").forEach(function (a) {
      a.classList.toggle("active", a.dataset.tab === (NAV_MAP[route.view] || route.view));
    });

    if (route.view === "home") renderHome();
    else if (route.view === "schedule") renderSchedule();
    else if (route.view === "standings") renderStandingsFull();
    else if (route.view === "roster") renderRoster();
    else if (route.view === "game") renderGameDetail(route.id);
    else if (route.view === "player") renderPlayerDetail(route.id);

    window.scrollTo(0, 0);
  }

  document.getElementById("backBtn").addEventListener("click", function () {
    history.back();
  });

  // ---------------------------------------------------------------- shared row builders
  function gameRow(g, opts) {
    opts = opts || {};
    var scoreText = g.score_us == null ? "-" : g.score_us + " : " + g.score_opp;
    var tagClass = g.result || g.status;
    var tagLabel = g.result ? RESULT_LABEL[g.result] : (STATUS_LABEL[g.status] || g.status);
    var loc = g.is_home ? "홈" : "원정";
    return (
      '<li><a href="#/game/' + g.id + '">' +
      '<div class="game-row">' +
      '<div class="game-left">' +
      '<span class="game-date">' + fmtDate(g.game_date) + (g.start_time ? " " + esc(g.start_time) : "") + "</span>" +
      '<span class="game-opp">vs ' + esc(g.opponent) + " (" + loc + ")</span>" +
      "</div>" +
      '<div class="game-right">' +
      '<span class="game-score">' + scoreText + "</span>" +
      '<span class="tag ' + tagClass + '">' + tagLabel + "</span>" +
      "</div></div></a></li>"
    );
  }

  function rosterCard(p) {
    var num = p.back_number != null ? p.back_number : "-";
    return (
      '<a href="#/player/' + p.id + '" class="player-card' + (p.is_captain ? " captain" : "") + '">' +
      '<span class="avatar">' + num + (p.is_captain ? '<span class="cap-badge">C</span>' : "") + "</span>" +
      '<span class="pname">' + esc(p.name) + "</span>" +
      "</a>"
    );
  }

  // ---------------------------------------------------------------- home
  function linescoreHtml(g) {
    if (!Array.isArray(g.linescore_us) || !Array.isArray(g.linescore_opp)) return "";
    var n = Math.max(g.linescore_us.length, g.linescore_opp.length);
    var headCells = "";
    for (var i = 0; i < n; i++) headCells += "<th>" + (i + 1) + "</th>";
    function rowCells(arr) {
      var cells = "";
      for (var i = 0; i < n; i++) {
        var v = arr[i];
        cells += "<td>" + (v == null ? "-" : v) + "</td>";
      }
      return cells;
    }
    return (
      '<div class="linescore-wrap"><table class="linescore"><thead><tr><th></th>' + headCells +
      '<th class="sep">R</th><th>H</th><th>E</th></tr></thead><tbody>' +
      '<tr><td class="team-abbr">삼성</td>' + rowCells(g.linescore_us) +
      '<td class="rhe r sep">' + (g.score_us == null ? "-" : g.score_us) + "</td>" +
      "<td>" + (g.hits_us == null ? "-" : g.hits_us) + "</td><td>" + (g.errors_us == null ? "-" : g.errors_us) + "</td></tr>" +
      '<tr><td class="team-abbr">' + esc(g.opponent) + "</td>" + rowCells(g.linescore_opp) +
      '<td class="rhe r sep">' + (g.score_opp == null ? "-" : g.score_opp) + "</td>" +
      "<td>" + (g.hits_opp == null ? "-" : g.hits_opp) + "</td><td>" + (g.errors_opp == null ? "-" : g.errors_opp) + "</td></tr>" +
      "</tbody></table></div>"
    );
  }

  function liveStateHtml(g) {
    if (g.status !== "live") return "";
    var inningLabel = g.current_inning
      ? g.current_inning + "회 " + (g.current_half === "bottom" ? "말" : "초")
      : "경기 진행 중";
    var outs = g.outs || 0;
    var outDots = "";
    for (var i = 0; i < 3; i++) outDots += '<span class="out-dot' + (i < outs ? " filled" : "") + '"></span>';
    return (
      '<div class="live-state">' +
      '<div class="diamond">' +
      '<span class="base-mark b2' + (g.base2 ? " on" : "") + '"></span>' +
      '<span class="base-mark b1' + (g.base1 ? " on" : "") + '"></span>' +
      '<span class="base-mark b3' + (g.base3 ? " on" : "") + '"></span>' +
      "</div>" +
      '<div><div class="live-inning">' + inningLabel + "</div>" +
      '<div class="live-outs">' + outs + "아웃<span class=\"out-dots\">" + outDots + "</span></div></div>" +
      "</div>"
    );
  }

  function heroCardHtml(g, hideDetailLink) {
    var badgeCls = "badge" + (g.status === "live" ? " live" : g.status === "scheduled" ? " outline" : "");
    var ourGameWatchable = g.game_date === todayStr() && (g.status === "scheduled" || g.status === "live");
    var btvLabel = ourGameWatchable ? "▶ TVING에서 시청" : "▶ TVING에서 오늘 다른 경기 보기";
    var btvNote = ourGameWatchable
      ? "TVING이 KBO 경기 온라인 중계권을 갖고 있습니다. 방송 시청이 어려우면 위 실시간 진행상황을 참고하세요."
      : "TVING에서는 삼성전 외 다른 구단 경기도 실시간으로 볼 수 있습니다.";
    return (
      '<div class="today-card">' +
      '<div class="today-head"><span class="' + badgeCls + '">' + (STATUS_LABEL[g.status] || g.status) + '</span>' +
      '<span class="muted">' + fmtDate(g.game_date) + "</span></div>" +
      '<div class="matchup">' +
      '<div class="team us"><span class="team-name">삼성</span><span class="score">' + (g.score_us == null ? "-" : g.score_us) + "</span></div>" +
      '<div class="vs-mid"><span class="state">' + (g.is_home ? "(홈)" : "(원정)") + "</span></div>" +
      '<div class="team opp"><span class="score">' + (g.score_opp == null ? "-" : g.score_opp) + '</span><span class="team-name">' + esc(g.opponent) + "</span></div>" +
      "</div>" +
      '<div class="today-meta"><span>' + esc(g.place || "") + '</span><span>' + esc(g.start_time || "") + "</span></div>" +
      (g.status === "cancelled"
        ? '<div class="cancel-note">🌧️ ' + esc(g.cancel_reason || "경기 취소") + "</div>"
        : "") +
      liveStateHtml(g) +
      linescoreHtml(g) +
      '<a class="btv-btn" href="' + TVING_URL + '" target="_blank" rel="noopener">' + btvLabel + "</a>" +
      '<p class="btv-note">' + btvNote + "</p>" +
      (hideDetailLink
        ? ""
        : '<a class="card-tap" href="#/game/' + g.id + '">' + (g.status === "finished" ? "선수별 기록 보기" : "경기 상세 보기") + " ›</a>") +
      "</div>"
    );
  }

  function nextCardHtml(g) {
    var days = Math.round((new Date(g.game_date) - new Date(todayStr())) / 86400000);
    return (
      '<div class="card">' +
      '<a href="#/game/' + g.id + '" class="next-card">' +
      "<div><div class=\"muted small\">다음 경기</div>" +
      '<div class="game-opp">vs ' + esc(g.opponent) + " (" + (g.is_home ? "홈" : "원정") + ")</div>" +
      '<div class="game-date">' + fmtDate(g.game_date) + (g.start_time ? " " + esc(g.start_time) : "") + "</div></div>" +
      '<div class="d-day">D-' + Math.max(days, 0) + "</div>" +
      "</a>" +
      '<a class="btv-btn small-btv" href="' + TVING_URL + '" target="_blank" rel="noopener">▶ TVING에서 오늘 경기 보기</a>' +
      "</div>"
    );
  }

  function renderHome() {
    var today = todayStr();
    var todayGame = DATA.games.find(function (g) { return g.game_date === today; });
    var homeToday = document.getElementById("homeToday");

    if (todayGame) {
      homeToday.innerHTML = heroCardHtml(todayGame);
    } else {
      var next = DATA.games
        .filter(function (g) { return g.game_date > today && g.status !== "cancelled"; })
        .sort(function (a, b) { return a.game_date.localeCompare(b.game_date); })[0];
      homeToday.innerHTML = next ? nextCardHtml(next) : '<div class="card empty-note">예정된 경기가 없습니다</div>';
    }

    var sorted = DATA.standings.slice().sort(function (a, b) { return a.rank - b.rank; });
    document.getElementById("homeStandingsMini").innerHTML = standingsTableHtml(sorted, true);

    // upcoming (오늘 제외, 취소 제외)
    var upcoming = DATA.games
      .filter(function (g) { return g.game_date > today && g.status !== "cancelled"; })
      .sort(function (a, b) { return a.game_date.localeCompare(b.game_date); })
      .slice(0, 3);
    document.getElementById("homeUpcoming").innerHTML =
      upcoming.length ? upcoming.map(function (g) { return gameRow(g); }).join("") : '<li class="empty-note">예정된 경기가 없습니다</li>';

    renderOtherGames();
    renderTickets();
  }

  function renderOtherGames() {
    var today = todayStr();
    var card = document.getElementById("otherGamesCard");
    var rows = DATA.otherGames.filter(function (g) { return g.game_date === today; });
    if (!rows.length) { card.hidden = true; return; }
    card.hidden = false;

    document.getElementById("otherGamesList").innerHTML = rows.map(function (g) {
      var scoreText = g.away_score == null ? "-" : g.away_score + " : " + g.home_score;
      var label = g.state_text || STATUS_LABEL[g.status] || g.status;
      return (
        '<li><div class="game-row" style="padding:10px">' +
        '<div class="game-left"><span class="game-opp">' + esc(g.away_team) + " vs " + esc(g.home_team) + "</span>" +
        '<span class="game-date">' + esc(label) + "</span></div>" +
        '<span class="game-score">' + scoreText + "</span>" +
        "</div></li>"
      );
    }).join("");
  }

  function renderTickets() {
    var rows = DATA.tickets.slice().sort(function (a, b) { return (a.sort_order || 0) - (b.sort_order || 0); });
    var list = document.getElementById("ticketList");
    if (!list) return;
    list.innerHTML = rows.length
      ? rows.map(function (t) {
          return (
            '<li><div class="game-row" style="padding:10px">' +
            '<div class="game-left"><span class="game-opp">' + esc(t.title) + '</span>' +
            '<span class="game-date">' + esc(t.event_date) + "</span></div>" +
            '<span class="tag ' + t.status + '">' + (TICKET_LABEL[t.status] || t.status) + "</span>" +
            "</div></li>"
          );
        }).join("")
      : '<li class="empty-note">등록된 일정이 없습니다</li>';
  }

  // ---------------------------------------------------------------- standings
  function standingsTableHtml(rows, mini) {
    if (!rows.length) return '<p class="empty-note">데이터가 없습니다</p>';
    var body = rows.map(function (r) {
      var cls = r.is_us ? "us-row" : "";
      var gb = r.games_behind == null || Number(r.games_behind) === 0 ? "-" : r.games_behind;
      return (
        '<tr class="' + cls + '">' +
        "<td>" + r.rank + "</td><td>" + esc(r.team) + "</td><td>" + r.games + "</td><td>" + r.wins + "</td><td>" + r.losses + "</td>" +
        "<td>" + (r.games - r.wins - r.losses) + "</td><td>" + Number(r.win_pct).toFixed(3).replace(/^0/, "") + "</td><td>" + gb + "</td>" +
        "</tr>"
      );
    }).join("");
    return (
      '<div class="table-wrap"><table class="standings' + (mini ? " mini" : "") + '"><thead><tr>' +
      "<th>순위</th><th>팀</th><th>경기</th><th>승</th><th>패</th><th>무</th><th>승률</th><th>게임차</th>" +
      "</tr></thead><tbody>" + body + "</tbody></table></div>"
    );
  }

  function renderStandingsFull() {
    var sorted = DATA.standings.slice().sort(function (a, b) { return a.rank - b.rank; });
    document.getElementById("standingsBody").innerHTML = sorted.length
      ? sorted.map(function (r) {
          var cls = r.is_us ? "us-row" : "";
          var gb = r.games_behind == null || Number(r.games_behind) === 0 ? "-" : r.games_behind;
          return (
            '<tr class="' + cls + '"><td>' + r.rank + "</td><td>" + esc(r.team) + "</td><td>" + r.games + "</td><td>" + r.wins +
            "</td><td>" + r.losses + "</td><td>" + (r.games - r.wins - r.losses) + "</td><td>" +
            Number(r.win_pct).toFixed(3).replace(/^0/, "") + "</td><td>" + gb + "</td></tr>"
          );
        }).join("")
      : '<tr><td colspan="8" class="loading">데이터가 없습니다</td></tr>';

    var us = sorted.find(function (r) { return r.is_us; });
    document.getElementById("standingsUpdated").textContent =
      us && us.updated_at ? "업데이트: " + new Date(us.updated_at).toLocaleString("ko-KR") : "";
  }

  // ---------------------------------------------------------------- schedule (calendar)
  function renderSchedule() {
    if (!calState) {
      var t = new Date();
      calState = { year: t.getFullYear(), month: t.getMonth() };
    }
    renderCalendar();
  }

  function renderCalendar() {
    var y = calState.year, m = calState.month;
    document.getElementById("calLabel").textContent = y + "년 " + (m + 1) + "월";

    var first = new Date(y, m, 1);
    var startOffset = first.getDay();
    var daysInMonth = new Date(y, m + 1, 0).getDate();
    var today = todayStr();

    var byDate = {};
    DATA.games.forEach(function (g) { byDate[g.game_date] = g; });

    var cells = [];
    for (var i = 0; i < startOffset; i++) cells.push('<div class="cal-cell pad"></div>');

    for (var d = 1; d <= daysInMonth; d++) {
      var dateStr = y + "-" + pad(m + 1) + "-" + pad(d);
      var g = byDate[dateStr];
      var cls = "cal-cell" + (dateStr === today ? " today" : "") + (g ? " has-game" : "");
      var dotCls = g ? (g.result || g.status) : "";
      var inner = '<span class="num">' + d + "</span>" + (g ? '<span class="dot ' + dotCls + '"></span>' : "");
      if (g) {
        cells.push('<a href="#/game/' + g.id + '" class="' + cls + '">' + inner + "</a>");
      } else {
        cells.push('<div class="' + cls + '">' + inner + "</div>");
      }
    }

    document.getElementById("calGrid").innerHTML = cells.join("");
  }

  document.getElementById("calPrev").addEventListener("click", function () {
    calState.month -= 1;
    if (calState.month < 0) { calState.month = 11; calState.year -= 1; }
    renderCalendar();
  });
  document.getElementById("calNext").addEventListener("click", function () {
    calState.month += 1;
    if (calState.month > 11) { calState.month = 0; calState.year += 1; }
    renderCalendar();
  });

  // ---------------------------------------------------------------- roster
  function renderRoster() {
    var groups = {};
    DATA.players.forEach(function (p) { (groups[p.position_group] = groups[p.position_group] || []).push(p); });

    var html = POSITION_ORDER.filter(function (k) { return groups[k]; }).map(function (key) {
      var players = groups[key].sort(function (a, b) { return (a.back_number || 99) - (b.back_number || 99); });
      return (
        '<div class="pos-section card"><h2>' + POSITION_LABELS[key] + '</h2>' +
        '<div class="roster-grid">' + players.map(rosterCard).join("") + "</div></div>"
      );
    }).join("");

    document.getElementById("rosterGroups").innerHTML = html || '<div class="card empty-note">등록된 선수가 없습니다</div>';
  }

  // ---------------------------------------------------------------- game detail
  function renderGameDetail(id) {
    var g = gamesById[id];
    var el = document.getElementById("gameDetail");
    if (!g) { el.innerHTML = '<div class="card empty-note">경기 정보를 찾을 수 없습니다</div>'; return; }

    var stats = (statsByGame[g.id] || []).slice().sort(function (a, b) { return (a.sort_order || 0) - (b.sort_order || 0); });

    function statListHtml(rows) {
      return '<ul class="stat-list">' + rows.map(function (s) {
        var pl = playersByName[s.player_name];
        var nameHtml = pl
          ? '<a href="#/player/' + pl.id + '"><span class="stat-name">' + esc(s.player_name) + "</span></a>"
          : '<span class="stat-name">' + esc(s.player_name) + "</span>";
        return (
          "<li><div>" + nameHtml + '<div class="stat-pos">' + esc(s.position || "") + "</div></div>" +
          '<div class="stat-line">' + esc(s.stat_line) + "</div></li>"
        );
      }).join("") + "</ul>";
    }

    var statsHtml;
    if (stats.length) {
      var batting = stats.filter(function (s) { return s.stat_type !== "pitching"; });
      var pitching = stats.filter(function (s) { return s.stat_type === "pitching"; });
      statsHtml =
        (batting.length ? "<h3 class=\"stat-sub\">타자</h3>" + statListHtml(batting) : "") +
        (pitching.length ? "<h3 class=\"stat-sub\">투수</h3>" + statListHtml(pitching) : "");
    } else if (g.status === "finished") {
      statsHtml = '<p class="empty-note">이 경기의 선수별 기록이 아직 등록되지 않았습니다.</p>';
    } else {
      statsHtml = '<p class="empty-note">경기 종료 후 선수별 기록이 표시됩니다.</p>';
    }

    var otherOnDate = DATA.otherGames.filter(function (o) { return o.game_date === g.game_date; });
    var otherHtml = otherOnDate.length
      ? '<div class="card"><h2>같은 날 다른 경기</h2><ul class="games-list">' +
        otherOnDate.map(function (o) {
          var scoreText = o.away_score == null ? "-" : o.away_score + " : " + o.home_score;
          var label = o.state_text || STATUS_LABEL[o.status] || o.status;
          return (
            '<li><div class="game-row" style="padding:10px">' +
            '<div class="game-left"><span class="game-opp">' + esc(o.away_team) + " vs " + esc(o.home_team) + "</span>" +
            '<span class="game-date">' + esc(label) + "</span></div>" +
            '<span class="game-score">' + scoreText + "</span></div></li>"
          );
        }).join("") + "</ul></div>"
      : "";

    el.innerHTML =
      heroCardHtml(g, true) +
      '<div class="card"><h2>선수별 기록</h2>' + statsHtml + "</div>" +
      otherHtml;
  }

  // ---------------------------------------------------------------- player detail
  function renderPlayerDetail(id) {
    var p = playersById[id];
    var el = document.getElementById("playerDetail");
    if (!p) { el.innerHTML = '<div class="card empty-note">선수 정보를 찾을 수 없습니다</div>'; return; }

    var chips = [p.position_detail, p.role, p.throws_bats].filter(Boolean)
      .map(function (c) { return '<span class="chip">' + esc(c) + "</span>"; }).join("");

    var recent = DATA.stats
      .filter(function (s) { return s.player_name === p.name; })
      .map(function (s) { return { s: s, g: gamesById[s.game_id] }; })
      .filter(function (x) { return x.g; })
      .sort(function (a, b) { return b.g.game_date.localeCompare(a.g.game_date); })
      .slice(0, 5);

    var recentHtml = recent.length
      ? '<ul class="stat-list">' + recent.map(function (x) {
          return (
            '<li><a href="#/game/' + x.g.id + '" style="display:flex;justify-content:space-between;width:100%">' +
            "<div><div class=\"stat-name\">vs " + esc(x.g.opponent) + '</div><div class="stat-pos">' + fmtDate(x.g.game_date) + "</div></div>" +
            '<div class="stat-line">' + esc(x.s.stat_line) + "</div></a></li>"
          );
        }).join("") + "</ul>"
      : '<p class="empty-note">최근 경기 기록이 없습니다.</p>';

    el.innerHTML =
      '<div class="detail-hero">' +
      '<div class="num-big">#' + (p.back_number != null ? p.back_number : "-") + "</div>" +
      '<div class="pname">' + esc(p.name) + (p.is_captain ? " (주장)" : "") + "</div>" +
      '<div class="psub">' + POSITION_LABELS[p.position_group] + "</div>" +
      '<div class="chip-row">' + chips + "</div>" +
      "</div>" +
      '<div class="card"><h2>시즌 기록</h2>' +
      "<p>" + (p.season_stat_summary ? esc(p.season_stat_summary) : '<span class="muted">아직 등록된 시즌 기록이 없습니다.</span>') + "</p>" +
      "</div>" +
      '<div class="card"><h2>최근 경기 기록</h2>' + recentHtml + "</div>";
  }

  // ---------------------------------------------------------------- 득점 실시간 알림
  // Web Notification API 기반. 앱(탭)이 열려서 30초마다 폴링되는 동안에만 동작한다 -
  // 완전히 앱을 끈 상태에서도 오는 "진짜 푸시 알림"은 서버(발송용 백엔드)가 따로
  // 필요해서 이번 버전에는 없다. 우선 앱 켜둔 상태에서의 실시간 알림부터 지원.
  var lastKnownGameState = null;

  function initNotifyBanner() {
    var banner = document.getElementById("notifyBanner");
    if (!banner) return;
    if (!("Notification" in window) || Notification.permission !== "default") {
      banner.hidden = true;
      return;
    }
    banner.hidden = false;
  }

  var notifyBtn = document.getElementById("notifyBtn");
  if (notifyBtn) {
    notifyBtn.addEventListener("click", function () {
      Notification.requestPermission().then(function () {
        initNotifyBanner();
      });
    });
  }

  function checkScoreNotify(todayGame) {
    if (!("Notification" in window) || Notification.permission !== "granted") return;
    if (!todayGame) { lastKnownGameState = null; return; }

    var key = todayGame.game_date + ":" + todayGame.opponent;
    var prev = lastKnownGameState && lastKnownGameState.key === key ? lastKnownGameState : null;

    if (prev) {
      if (
        (prev.score_us !== todayGame.score_us || prev.score_opp !== todayGame.score_opp) &&
        todayGame.score_us != null
      ) {
        new Notification("🦁 삼성 라이온즈", {
          body: "삼성 " + todayGame.score_us + " : " + todayGame.score_opp + " " + todayGame.opponent +
                (todayGame.status === "finished" ? " (경기 종료)" : ""),
          icon: "icons/icon-192.png",
          tag: "lions-score",
        });
      } else if (prev.status !== todayGame.status && todayGame.status === "live") {
        new Notification("🦁 삼성 라이온즈", { body: "오늘 경기가 시작됐습니다!", icon: "icons/icon-192.png", tag: "lions-score" });
      } else if (prev.status !== todayGame.status && todayGame.status === "finished") {
        new Notification("🦁 삼성 라이온즈", {
          body: "경기 종료: 삼성 " + todayGame.score_us + " : " + todayGame.score_opp + " " + todayGame.opponent,
          icon: "icons/icon-192.png",
          tag: "lions-score",
        });
      }
    }

    lastKnownGameState = { key: key, score_us: todayGame.score_us, score_opp: todayGame.score_opp, status: todayGame.status };
  }

  // ---------------------------------------------------------------- data load
  function showError(msg) {
    var main = document.getElementById("main");
    var box = document.getElementById("errBox");
    if (!box) { box = document.createElement("div"); box.id = "errBox"; box.className = "error-box"; main.prepend(box); }
    box.textContent = msg;
  }
  function clearError() { var box = document.getElementById("errBox"); if (box) box.remove(); }

  async function loadAll() {
    var refreshBtn = document.getElementById("refreshBtn");
    refreshBtn.classList.add("spinning");
    try {
      var res = await Promise.all([
        sb.from("standings").select("*"),
        sb.from("games").select("*"),
        sb.from("ticket_events").select("*"),
        sb.from("players").select("*"),
        sb.from("game_player_stats").select("*"),
        sb.from("other_games").select("*"),
      ]);
      var err = res.find(function (r) { return r.error; });
      if (err) throw err.error;

      DATA.standings = res[0].data || [];
      DATA.games = res[1].data || [];
      DATA.tickets = res[2].data || [];
      DATA.players = res[3].data || [];
      DATA.stats = res[4].data || [];
      DATA.otherGames = res[5].data || [];

      gamesById = {}; DATA.games.forEach(function (g) { gamesById[g.id] = g; });
      statsByGame = {};
      DATA.stats.forEach(function (s) { (statsByGame[s.game_id] = statsByGame[s.game_id] || []).push(s); });
      playersById = {}; playersByName = {};
      DATA.players.forEach(function (p) { playersById[p.id] = p; playersByName[p.name] = p; });

      var todayGame = DATA.games.find(function (g) { return g.game_date === todayStr(); });
      checkScoreNotify(todayGame);

      clearError();
      renderRoute();
    } catch (e) {
      console.error(e);
      showError("데이터를 불러오지 못했습니다. 네트워크 상태를 확인하고 다시 시도해주세요.");
    } finally {
      refreshBtn.classList.remove("spinning");
    }
  }

  document.getElementById("refreshBtn").addEventListener("click", loadAll);
  window.addEventListener("hashchange", renderRoute);

  initNotifyBanner();
  loadAll();
  setInterval(loadAll, 30000); // 경기 시간대엔 크롤러가 5분마다 갱신, 화면은 30초마다 재조회

  if ("serviceWorker" in navigator) {
    window.addEventListener("load", function () {
      navigator.serviceWorker.register("sw.js").catch(function () {});
    });
  }
})();
