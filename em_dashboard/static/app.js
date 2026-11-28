/* EM 평가 현황 대시보드 - 화면
 * 계산 규칙은 rules.js 에만 있다. 여기서는 그 결과를 그리기만 한다.
 * window.EMBEDDED_DATA 가 있으면 서버 없이 그 데이터로 동작한다 (샘플 미리보기용).
 */
(function () {
  'use strict';
  var R = window.EMRules;
  var STAGE_COLOR = { WAFER_WAIT: 'var(--st-wafer)', PKG_LINE: 'var(--st-pkg)', TEST_WAIT: 'var(--st-wait)', RUNNING: 'var(--st-run)', DONE: 'var(--st-done)' };
  var STAGE_LABEL = {};
  R.STAGES.forEach(function (s) { STAGE_LABEL[s.id] = s.label; });
  var ACTIVE = ['PKG_LINE', 'TEST_WAIT', 'RUNNING'];

  var state = {
    data: null, recs: [], today: 0, revision: null,
    f: { process: '', tester: '', engineer: '', equip: '', q: '', stage: '' },
    tab: 'list', sort: { key: 'List_no', dir: 1 },
    editing: null, pendingReload: false, history: [], historyLoaded: false, localHistory: [],
    pending: null, saving: false, delOpen: false
  };
  var embedded = !!window.EMBEDDED_DATA;

  function $(id) { return document.getElementById(id); }
  function esc(s) {
    return String(s === null || s === undefined ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function fmtDay(n) { return new Date(n * 864e5).toISOString().slice(0, 10); }
  function cell(v) { return R.has(v) ? esc(v) : '<span class="muted">-</span>'; }
  function tags(v, tone) {
    if (!R.has(v)) return '<span class="muted">-</span>';
    return String(v).split('|').map(function (t) { return '<span class="tag ' + (tone || '') + '">' + esc(t) + '</span>'; }).join('');
  }
  function pill(stage) {
    return '<span class="pill" style="--sc:' + STAGE_COLOR[stage] + '"><i></i>' + esc(STAGE_LABEL[stage]) + '</span>';
  }
  // 행 구분 키 칸 (config.ini [data] row_key). 표시는 Test 를 빼고 나머지만
  function keyFields() { return (state.data && state.data.config.key_fields) || ['Test', 'List_no']; }
  function keyLabel(k) {
    var kf = keyFields(), parts = String(k).split('|');
    var shown = parts.filter(function (p, i) { return kf[i] !== 'Test' || kf.length === 1; });
    return shown.join(' / ');
  }
  function ageText(x) { return x.aging === null ? '<span class="muted">-</span>' : '<span title="' + esc(STAGE_LABEL[x.stage] + ' 도착 ' + x.since) + '">' + x.aging + '일째</span>'; }
  function store(k, v) { try { localStorage.setItem('emdash.' + k, v); } catch (e) { /* 저장 불가 환경 */ } }
  function recall(k) { try { return localStorage.getItem('emdash.' + k); } catch (e) { return null; } }

  // ---------- 데이터 ----------
  function load() {
    if (embedded) return Promise.resolve(window.EMBEDDED_DATA);
    return fetch('/api/data', { cache: 'no-store' }).then(function (r) {
      if (!r.ok) throw new Error('서버 응답 ' + r.status);
      return r.json();
    });
  }
  function apply(data) {
    if (state.editing) { state.pendingReload = true; return; }
    data.deleted = data.deleted || [];
    state.data = data;
    state.revision = data.meta.revision;
    var asOf = data.meta.as_of ? R.parseDate(data.meta.as_of) : null;
    state.today = asOf !== null && !isNaN(asOf) ? asOf : R.dayNumber(new Date());
    recompute();
    if (!embedded && !state.timer) state.timer = setInterval(poll, 1000 * (data.config.poll_seconds || 10));
  }
  function reload() {
    if (state.editing) { state.pendingReload = true; return; }
    state.historyLoaded = false;
    load().then(apply).catch(function (e) { showError('데이터를 불러오지 못했습니다: ' + e.message + '. 서버가 켜져 있는지 확인하세요.'); });
  }
  function poll() {
    if (embedded || !state.data) return;
    fetch('/api/status', { cache: 'no-store' }).then(function (r) { return r.json(); }).then(function (s) {
      if (s.revision !== state.revision) reload();
      else if (s.error !== state.data.meta.error) { state.data.meta.error = s.error; renderTop(); }
    }).catch(function () { showError('서버와 연결이 끊겼습니다. 서버 PC 를 확인하세요. 화면은 마지막 데이터입니다.'); });
  }
  function showError(msg) {
    var b = $('banner');
    b.className = 'banner err'; b.textContent = msg; b.hidden = false;
  }

  // ---------- 필터 ----------
  // keepDel: 삭제 예정 행도 포함 (현황 표에서 취소선으로 보여줄 때만)
  function filtered(ignoreStage, keepDel) {
    var f = state.f, q = f.q.trim().toLowerCase();
    return state.recs.filter(function (x) {
      var r = x.row;
      if (r._pdel && !keepDel) return false;
      if (f.process && r.Process !== f.process) return false;
      if (f.tester && r.Tester !== f.tester) return false;
      if (f.equip && (r['설비'] || '') !== f.equip) return false;
      if (f.engineer && r.Engineer !== f.engineer) return false;
      if (!ignoreStage && f.stage && x.stage !== f.stage) return false;
      if (q) {
        var hay = [r.Sdm_no, r.List_no, r.Lot_ID, r.Structure, r.Vehicle, r['비고'], r.Process, r['설비'], r.Oven, r.Tester].join(' ').toLowerCase();
        if (hay.indexOf(q) < 0) return false;
      }
      return true;
    });
  }
  function uniq(field) {
    var s = {};
    state.recs.forEach(function (x) { if (R.has(x.row[field])) s[x.row[field]] = 1; });
    return Object.keys(s).sort();
  }
  function fillFilterOptions() {
    [['process', 'Process'], ['tester', 'Tester'], ['engineer', 'Engineer'], ['equip', '설비']].forEach(function (p) {
      var sel = $('f-' + p[0]), cur = state.f[p[0]];
      sel.innerHTML = '<option value="">전체</option>' + uniq(p[1]).map(function (v) {
        return '<option' + (v === cur ? ' selected' : '') + '>' + esc(v) + '</option>';
      }).join('');
    });
  }

  // ---------- 그리기 ----------
  function render() { renderTop(); renderTabs(); renderView(); }

  function renderTop() {
    var m = state.data.meta;
    var bits = [];
    bits.push('<span><span class="dot' + (m.error ? ' err' : '') + '"></span>' + (m.error ? '원본 연결 오류' : '원본 연결 정상') + '</span>');
    bits.push('<span>기준일 <b class="num">' + fmtDay(state.today) + '</b></span>');
    bits.push('<span>마지막 반영 <b class="num">' + esc(m.loaded_at || '-') + '</b></span>');
    bits.push('<span>' + state.data.rows.length + '행' + (state.data.quarantine.length ? ' · 격리 ' + state.data.quarantine.length + '줄' : '') + '</span>');
    $('meta').innerHTML = bits.join('');
    $('src').textContent = '원본: ' + (m.source || '-') + (m.encoding ? ' (' + m.encoding + ')' : '') + ' · 읽기 전용 · v' + (m.version || '');
    var b = $('banner');
    if (m.demo) { b.className = 'banner info'; b.textContent = '샘플 데이터 미리보기입니다. 실제 데이터가 아니며, 기준일은 ' + m.as_of + ' 로 고정되어 있습니다.'; b.hidden = false; }
    else if (m.error) { showError(m.error); }
    else b.hidden = true;
  }

  var TABS = [
    { id: 'list', label: '현황 표' },
    { id: 'summary', label: '현황 요약' },
    { id: 'equip', label: 'Oven 사용' },
    { id: 'people', label: '담당자 · 공정' },
    { id: 'quality', label: '데이터 확인' },
    { id: 'history', label: '변경 이력' }
  ];
  function renderTabs() {
    var recs = filtered(true);
    var bad = recs.filter(function (x) { return x.flags.length; }).length + state.data.quarantine.length;
    $('tabs').innerHTML = TABS.map(function (t) {
      var c = '';
      if (t.id === 'quality') c = '<span class="cnt' + (bad ? ' alert' : '') + '">' + bad + '</span>';
      if (t.id === 'list') c = '<span class="cnt">' + filtered(false).length + '</span>';
      return '<button class="tab" role="tab" data-tab="' + t.id + '" aria-selected="' + (state.tab === t.id) + '">' + t.label + c + '</button>';
    }).join('');
  }

  function renderView() {
    var v = $('view');
    var fn = { summary: viewSummary, equip: viewEquip, people: viewPeople, list: viewList, quality: viewQuality, history: viewHistory }[state.tab];
    v.innerHTML = fn();
  }

  function viewSummary() {
    var recs = filtered(true);
    var s = R.summarize(recs, state.today);
    var pipe = R.STAGES.map(function (st) {
      var inSt = recs.filter(function (x) { return x.stage === st.id && x.aging !== null; });
      var sub;
      if (st.id === 'DONE') sub = '<span class="chip ok">이번 주 ' + s.outThisWeek + '건 완료</span>';
      else if (inSt.length) {
        var mx = Math.max.apply(null, inSt.map(function (x) { return x.aging; }));
        var avg = Math.round(inSt.reduce(function (a, x) { return a + x.aging; }, 0) / inSt.length);
        sub = '<span class="muted">도착 후 평균 ' + avg + '일 · 최장 ' + mx + '일</span>';
      } else sub = '<span class="muted">' + (st.id === 'WAFER_WAIT' ? '날짜 입력 전' : '해당 없음') + '</span>';
      return '<button class="stage" style="--sc:' + STAGE_COLOR[st.id] + '" data-stage="' + st.id + '" title="' + st.label + ' 목록 보기">' +
        '<div class="name"><i></i>' + st.label + '</div>' +
        '<div class="big">' + s.byStage[st.id] + '<span>건</span></div>' +
        '<div class="sub">' + sub + '</div></button>';
    }).join('');
    var act = recs.filter(function (x) { return ACTIVE.indexOf(x.stage) >= 0 && x.aging !== null; });
    var longest = act.length ? Math.max.apply(null, act.map(function (x) { return x.aging; })) : 0;
    var kpis =
      kpi('이번 주 EM 투입', s.inThisWeek, '건', false, '최근 7일 Test_in') +
      kpi('가동 중 Oven', s.runningBoards, '대', false, 'EM 진행중 DUT 보드 합 (Oven 1대 = 보드 1장)') +
      kpi('가장 오래 머문 건', longest, '일째', false, 'PKG 진행 · Test 대기 · 진행중 중') +
      kpi('데이터 확인 필요', s.flagged + state.data.quarantine.length, '건', (s.flagged + state.data.quarantine.length) > 0, '날짜·수량·중복·깨진 줄');

    var top = act.slice().sort(function (a, b) { return b.aging - a.aging; }).slice(0, 8);
    var topRows = top.map(function (x) {
      return '<tr><td>' + pill(x.stage) + '</td><td class="n">' + x.aging + '일째</td><td class="mono">' + esc(x.since) +
        '</td><td class="mono">' + cell(x.row.List_no) + '</td><td>' + cell(x.row.Process) + '</td><td class="mono">' + cell(x.row['설비']) + '</td><td>' + cell(x.row.Tester) + '</td></tr>';
    }).join('');

    return '<div class="pipeline">' + pipe + '</div>' +
      '<div class="kpis">' + kpis + '</div>' +
      '<div class="grid2">' +
      '<section class="panel strip"><h2>단계 도착 후 경과일</h2><p class="desc">점 하나가 한 건입니다. 지금 단계에 도착한 날부터 며칠째인지 보여 줍니다.</p>' + stripChart(recs) + '</section>' +
      '<section class="panel"><h2>가장 오래 머문 건</h2><p class="desc">진행 단계(PKG 진행 · Test 대기 · 진행중)에서 도착 후 경과일이 긴 순. 상위 8건.</p>' +
      (topRows ? '<div class="tablebox"><table><thead><tr><th>단계</th><th class="n">경과</th><th>도착일</th><th>List_no</th><th>Process</th><th>설비</th><th>작업자</th></tr></thead><tbody>' + topRows + '</tbody></table></div>' : '<p class="muted">진행 중인 건이 없습니다.</p>') +
      '</section></div>';
  }
  function kpi(label, v, unit, alert, hint) {
    return '<div class="kpi' + (alert ? ' alert' : '') + '"><div class="l">' + label + '</div><div class="v">' + v + '<small>' + unit + '</small></div><div class="foot">' + hint + '</div></div>';
  }

  function stripChart(recs) {
    var W = 640, L = 96, Rm = 24, rowH = 52, T = 28;
    var max = 7;
    recs.forEach(function (x) { if (x.aging !== null && ACTIVE.indexOf(x.stage) >= 0) max = Math.max(max, x.aging); });
    var step = max <= 30 ? 7 : max <= 70 ? 14 : 30;
    max = Math.ceil(max / step) * step;
    var X = function (d) { return L + (W - L - Rm) * d / max; };
    var H = T + rowH * ACTIVE.length + 8;
    var out = '<svg viewBox="0 0 ' + W + ' ' + H + '" role="img" aria-label="단계별 체류일 분포">';
    for (var t = 0; t <= max; t += step) {
      out += '<line class="axis" x1="' + X(t) + '" x2="' + X(t) + '" y1="' + (T - 6) + '" y2="' + (H - 8) + '"/>' +
        '<text x="' + X(t) + '" y="' + (T - 12) + '" text-anchor="middle">' + t + '일</text>';
    }
    ACTIVE.forEach(function (st, i) {
      var y = T + rowH * i + rowH / 2;
      out += '<text class="lbl" x="0" y="' + (y + 4) + '">' + STAGE_LABEL[st] + '</text>';
      var pts = recs.filter(function (x) { return x.stage === st && x.aging !== null; }).sort(function (a, b) { return a.aging - b.aging; });
      var lastX = -99, lane = 0;
      pts.forEach(function (x) {
        var px = X(x.aging);
        lane = (px - lastX < 9) ? (lane + 1) % 3 : 0;
        lastX = px;
        var py = y + (lane === 0 ? 0 : lane === 1 ? -9 : 9);
        out += '<circle class="ok" cx="' + px.toFixed(1) + '" cy="' + py + '" r="5"><title>' +
          esc('List ' + x.row.List_no + ' · ' + x.row.Process + ' · ' + x.aging + '일') + '</title></circle>';
      });
      if (!pts.length) out += '<text x="' + L + '" y="' + (y + 4) + '">해당 없음</text>';
    });
    return out + '</svg>';
  }

  // 설비 한 대의 Oven 칸: 번호마다 비었는지, 어느 List 가 쓰는지 (Oven 칸에 입력한 번호 기준)
  function ovenMap(e) {
    var occ = e.occupied || {};
    var nums = Object.keys(occ).map(Number);
    var n = Math.max(e.capacity || 0, nums.length ? Math.max.apply(null, nums) : 0);
    if (!n) return '';
    var cells = '';
    for (var i = 1; i <= n; i++) {
      var who = occ[i] || [];
      var cls = who.length > 1 ? 'oc clash' : who.length ? 'oc on' : 'oc';
      if (e.capacity && i > e.capacity) cls += ' out';
      var tip = 'Oven ' + i + ': ' + (who.length ? 'List ' + who.join(', ') + (who.length > 1 ? ' (겹침)' : '') : '비어 있음');
      cells += '<span class="' + cls + '" title="' + esc(tip) + '">' + i + (who.length ? '<small>' + esc(who.join(',')) + '</small>' : '') + '</span>';
    }
    var known = nums.length;
    var miss = e.boards - known;
    return '<div class="ovmap" aria-label="' + esc(e.tester + ' Oven 배치') + '">' + cells + '</div>' +
      (miss > 0 ? '<div class="ovnote">사용 ' + e.boards + '대 중 ' + miss + '대는 Oven 번호가 입력되지 않았습니다</div>' : '');
  }
  function viewEquip() {
    var recs = filtered(true);
    var ovens = state.data.config.ovens || {};
    var load = R.equipmentLoad(recs, ovens, '설비').filter(function (e) { return e.capacity || e.running || e.waiting; });
    if (!load.length) return '<div class="panel"><h2>설비별 Oven 사용</h2><p class="desc">현황 표의 "설비" 칸에 설비명을 입력하면 여기에 설비별 사용 Oven 이 나옵니다.</p></div>';
    var maxUsed = Math.max.apply(null, load.map(function (e) { return e.capacity || e.boards; }).concat([1]));
    var rows = load.map(function (e) {
      var total = e.capacity, used = e.boards, none = e.tester === '(미지정)';
      var over = total && used > total;
      var w = total ? Math.min(100, used / total * 100) : used / maxUsed * 100;
      var txt = total
        ? '사용 ' + used + ' / ' + total + '대 · <b>빈 Oven ' + Math.max(0, total - used) + '대</b>' + (over ? ' · <b class="late">대수보다 많음, 확인 필요</b>' : '')
        : '사용 ' + used + '대' + (none ? '' : ' <span class="muted">(전체 대수 미설정)</span>');
      txt += ' · 진행 ' + e.running + '건' + (e.waiting ? ' · 투입 대기 ' + e.waiting + '건' : '');
      var name = none ? '<button class="linkish" data-equip-none="1" title="설비를 입력하지 않은 건 보기">설비 미입력</button>' : esc(e.tester);
      return '<div class="t">' + name + '</div><div class="bar' + (over ? ' crit' : '') + (total ? '' : ' nocap') + '" role="img" aria-label="' + esc(e.tester + ' ' + txt.replace(/<[^>]+>/g, '')) + '"><span style="width:' + w.toFixed(1) + '%"></span></div><div class="s">' + txt + '</div>' +
        (none ? '' : ovenMap(e));
    }).join('');
    var anySet = Object.keys(ovens).length > 0;
    return '<section class="panel"><h2>설비별 Oven 사용</h2><p class="desc">Oven 1대 = DUT 보드 1장 = 시료 16개. 사용 중 Oven = EM 진행중 건의 진행 보드 수 합 (DUT "8/8" = 진행 8 / 계획 8 보드, 앞 숫자). ' +
      '설비와 Oven 번호는 현황 표의 "설비", "Oven(DUT)" 칸에 작업자가 입력합니다. 아래 칸 하나가 Oven 한 대이고, 칸 안의 작은 숫자는 그 Oven 을 쓰는 List_no 입니다(빨강 = 두 건이 겹침). ' +
      (anySet ? '전체 대수는 config.ini [equipment_ovens] 값입니다.' : '설비별 전체 Oven 대수(5~24)를 config.ini [equipment_ovens] 에 적으면 빈 Oven 수가 나옵니다.') +
      ' 투입 대기 = 그 설비로 정해진 Test 대기 건.</p><div class="eq">' + rows + '</div></section>';
  }

  function viewPeople() {
    return '<div class="grid2">' + crossTable('Engineer', '담당자') + crossTable('Process', '공정') + '</div><p class="foot">숫자를 누르면 해당 건 목록으로 이동합니다.</p>';
  }
  function crossTable(field, title) {
    var recs = filtered(true);
    var keys = {};
    recs.forEach(function (x) { keys[x.row[field] || '(미지정)'] = 1; });
    var head = '<tr><th>' + title + '</th>' + R.STAGES.map(function (s) { return '<th class="n">' + s.label + '</th>'; }).join('') + '<th class="n">계</th></tr>';
    var body = Object.keys(keys).sort().map(function (k) {
      var tot = 0;
      var cells = R.STAGES.map(function (s) {
        var m = recs.filter(function (x) { return (x.row[field] || '(미지정)') === k && x.stage === s.id; });
        tot += m.length;
        if (!m.length) return '<td class="c zero">·</td>';
        return '<td class="c" data-x="' + esc(field) + '" data-v="' + esc(k) + '" data-stage="' + s.id + '">' + m.length + '</td>';
      }).join('');
      return '<tr><td class="mono">' + esc(k) + '</td>' + cells + '<td class="n"><b>' + tot + '</b></td></tr>';
    }).join('');
    return '<section class="panel"><h2>' + title + '별 단계 현황</h2><div class="tablebox xt"><table><thead>' + head + '</thead><tbody>' + body + '</tbody></table></div></section>';
  }

  // 사용자가 지정한 현황 표 컬럼 순서 (앞에 단계·체류, 끝에 확인 표시)
  var COLS = [
    { k: 'stage', l: '단계', get: function (x) { return R.STAGES.map(function (s) { return s.id; }).indexOf(x.stage); }, html: function (x) { return pill(x.stage); } },
    { k: 'aging', l: '경과', n: true, tip: '지금 단계에 도착한 날부터 며칠째', get: function (x) { return x.aging === null ? -1 : x.aging; }, html: ageText },
    { k: 'Test' }, { k: 'Sdm_no', m: 1 }, { k: 'List_no', m: 1, num: 1 }, { k: 'Process' },
    { k: 'Test_in', m: 1, date: 1 }, { k: 'Test_out', m: 1, date: 1 }, { k: 'DUT', m: 1, tip: '진행 보드 / 계획 보드 (보드 1장 = 시료 16개 = Oven 1대)' }, { k: 'Total수량', n: true, num: 1 },
    { k: 'Wafer_in', m: 1, date: 1 }, { k: 'PKG_Line', m: 1, date: 1 }, { k: 'PKG_in', m: 1, date: 1 },
    { k: 'Vehicle', m: 1 }, { k: 'Tester', m: 1, tip: '작업자 이니셜' }, { k: '설비', m: 1 }, { k: 'Oven', m: 1, l: 'Oven(DUT)' }, { k: 'Engineer' },
    { k: '비고', wrap: 1 }, { k: 'Lot_ID', tags: 1 }, { k: 'Structure', tags: 1 },
    { k: 'flags', l: '확인', get: function (x) { return x.flags.length; }, html: function (x) { return x.flags.length ? '<span class="flag" title="' + esc(x.flags.join('\n')) + '">⚠ ' + x.flags.length + '</span>' : ''; } }
  ];
  function editable() { return (state.data && state.data.config.editable) || ['Sdm_no', 'Test_in', 'Test_out', 'Wafer_in', 'PKG_Line', 'PKG_in', '비고', '설비', 'Oven']; }
  function sortVal(c, x) {
    if (c.get) return c.get(x);
    var v = x.row[c.k];
    if (c.num) { var n = parseFloat(v); return isNaN(n) ? -Infinity : n; }
    return R.has(v) ? String(v) : '';
  }
  function viewList() {
    var recs = filtered(false, true).slice();
    var c = COLS.filter(function (c) { return c.k === state.sort.key; })[0] || COLS[1];
    var dir = state.sort.dir;
    recs.sort(function (a, b) {
      var va = sortVal(c, a), vb = sortVal(c, b);
      if (va < vb) return -dir; if (va > vb) return dir;
      return (parseFloat(a.row.List_no) || 0) - (parseFloat(b.row.List_no) || 0);
    });
    var base = filtered(true);
    var ed = editable();
    var s = R.summarize(base, state.today);
    var qty = function (list) { return list.reduce(function (a, x) { var q = parseFloat(x.row['Total수량']); return a + (isNaN(q) ? 0 : q); }, 0); };
    var runQ = qty(base.filter(function (x) { return x.stage === 'RUNNING'; }));
    var ovenRun = s.runningBoards;
    var cards = [
      { st: '', l: '전체 등록 건수', v: base.length, sub: '총 ' + qty(base).toLocaleString() + ' 시료', c: 'var(--ink)' },
      { st: 'RUNNING', l: 'EM 진행중 (Test_in)', v: s.byStage.RUNNING, sub: 'Oven ' + ovenRun + '대 · ' + runQ.toLocaleString() + ' 시료', c: 'var(--st-run)' },
      { st: 'TEST_WAIT', l: 'Test 대기 (PKG_in)', v: s.byStage.TEST_WAIT, sub: 'Oven 투입 대기', c: 'var(--st-wait)' },
      { st: 'PKG_LINE', l: 'PKG 진행 (본딩)', v: s.byStage.PKG_LINE, sub: 'Ceramic PKG 본딩', c: 'var(--st-pkg)' },
      { st: 'DONE', l: '평가 완료 (Test_out)', v: s.byStage.DONE, sub: '이번 주 ' + s.outThisWeek + '건', c: 'var(--st-done)' },
      { st: 'WAFER_WAIT', l: '입고 대기', v: s.byStage.WAFER_WAIT, sub: '날짜 미입력', c: 'var(--st-wafer)' }
    ];
    var kpi = '<section class="xkpi">' + cards.map(function (k) {
      return '<button class="xk" data-chip="' + k.st + '" aria-pressed="' + (state.f.stage === k.st) + '"><span class="l">' + k.l + '</span><span class="v" style="color:' + k.c + '">' + k.v + '<small>건</small></span><span class="sub">' + k.sub + '</span></button>';
    }).join('') + '</section>';
    var chipDefs = [{ id: '', l: '전체' }, { id: 'RUNNING', l: 'EM 진행중' }, { id: 'TEST_WAIT', l: 'Test 대기' }, { id: 'PKG_LINE', l: 'PKG 진행' }, { id: 'DONE', l: '평가완료' }, { id: 'WAFER_WAIT', l: '입고대기' }];
    var chips = chipDefs.map(function (d) {
      return '<button class="xchip" data-chip="' + d.id + '" aria-pressed="' + (state.f.stage === d.id) + '">' + d.l + '</button>';
    }).join('');
    var head = '<th class="rn">#</th><th class="act">행</th>' + COLS.map(function (c) {
      var on = state.sort.key === c.k;
      var isEd = ed.indexOf(c.k) >= 0;
      return '<th class="sort' + (c.n ? ' n' : '') + (isEd ? ' edh' : '') + '" data-sort="' + esc(c.k) + '"' + (isEd ? ' title="작업자 직접 입력' + (c.k === '설비' ? ' (원본 .txt 에 없는 칸)' : c.k === 'Oven' ? ': 이 평가가 들어간 Oven 번호. 예) 1|2|3 또는 1-8 (원본 .txt 에 없는 칸)' : '') + '"' : c.tip ? ' title="' + esc(c.tip) + '"' : '') + '>' + esc(c.l || c.k) + (isEd ? ' ✎' : '') + (on ? '<span class="ar">' + (state.sort.dir > 0 ? '▲' : '▼') + '</span>' : '') + '</th>';
    }).join('');
    var body = recs.map(function (x, i) {
      var r = x.row;
      var trc = r._padd ? 'p-add' : r._pdel ? 'p-del' : r._prestore ? 'p-res' : '';
      var act;
      if (r._dup) act = '<span class="muted" title="행 구분 키(' + esc(keyFields().join('+')) + ')가 중복되거나 비어 있어 바꿀 수 없습니다">-</span>';
      else if (r._padd) act = '<button class="rbtn" data-del="' + esc(r._key) + '" title="추가 예정 취소">빼기</button>';
      else if (r._pdel) act = '<button class="rbtn undo" data-del="' + esc(r._key) + '" title="삭제 예정 취소">되돌리기</button>';
      else if (r._prestore) act = '<button class="rbtn undo" data-del="' + esc(r._key) + '" title="되살리기 취소">되돌리기</button>';
      else act = '<button class="rbtn" data-del="' + esc(r._key) + '" title="저장할 때 삭제합니다 (나중에 되살릴 수 있음)">삭제</button>';
      var mark = r._padd ? '<span class="pmark add">추가 예정</span>' : r._pdel ? '<span class="pmark del">삭제 예정</span>' : r._prestore ? '<span class="pmark res">되살림 예정</span>' : '';
      return '<tr' + (trc ? ' class="' + trc + '"' : '') + '><td class="rn">' + (i + 1) + '</td><td class="act">' + act + mark + '</td>' + COLS.map(function (c) {
        if (c.k === 'stage') return '<td><span class="spill s-' + x.stage + '">' + esc(STAGE_LABEL[x.stage]) + '</span></td>';
        if (c.html) return '<td' + (c.n ? ' class="n"' : '') + '>' + c.html(x) + '</td>';
        var v = r[c.k];
        if (c.tags) return '<td>' + tags(v, c.k === 'Structure' ? 'tag2' : '') + '</td>';
        var cls = [c.m ? 'mono' : '', c.n ? 'n' : '', c.wrap ? 'wrap' : '', c.k === 'Test' ? 'b' : ''];
        var attrs = '';
        if (ed.indexOf(c.k) >= 0) {
          if (r._dup) { cls.push('ed-off'); attrs = ' title="행 구분 키(' + esc(keyFields().join('+')) + ')가 중복되거나 비어 있어 입력할 수 없습니다"'; }
          else if (r._pdel) { cls.push('ed-off'); attrs = ' title="삭제 예정 행입니다"'; }
          else {
            cls.push('ed');
            var tip = [];
            if (r._src && c.k in r._src) { cls.push('chg'); tip.push('원본 값: ' + (R.has(r._src[c.k]) ? r._src[c.k] : '(빈 값)')); }
            if (r._by && r._by[c.k]) tip.push('입력: ' + r._by[c.k]);
            if (c.k in r._pset) { cls.push('pend'); tip.push('저장 전 (서버 값: ' + (R.has(r._pset[c.k]) ? r._pset[c.k] : '빈 값') + ')'); }
            if (r._pstale[c.k]) { cls.push('pstale'); tip.push('그 사이 다른 사람이 이 칸을 바꿨습니다. 저장할 때 어떻게 할지 묻습니다'); }
            tip.push('클릭하여 직접 입력');
            attrs = ' data-key="' + esc(r._key) + '" data-f="' + esc(c.k) + '" tabindex="0" title="' + esc(tip.join('\n')) + '"';
          }
        }
        return '<td class="' + cls.join(' ').trim() + '"' + attrs + '>' + cell(v) + '</td>';
      }).join('') + '</tr>';
    }).join('');
    var n = pendCount();
    var tools = '<button class="btn" id="addrow">＋ 행 추가</button>' +
      '<button class="btn save' + (n ? ' on' : '') + '" id="save"' + (n && !state.saving ? '' : ' disabled') + ' title="내 변경을 서버에 한 번에 반영합니다">' + (state.saving ? '저장 중...' : '저장' + (n ? ' (' + n + ')' : '')) + '</button>' +
      (n ? '<button class="btn" id="discard">변경 취소</button>' : '') +
      (embedded ? '' : '<a class="btn dark" href="/api/export/txt" title="원본과 같은 형식(17컬럼, 빈 값 .)으로 입력값을 합친 파일을 내려받습니다. 원본 파일은 바뀌지 않습니다">TXT 내보내기</a><button class="btn green" id="csv">엑셀(.csv)</button>');
    return kpi +
      '<section class="xbar"><div class="xchips">' + chips + '</div><div class="xtools">' + tools + '</div></section>' +
      '<section class="xl">' +
      '<div class="xl-strip"><span><b class="num">표시 ' + recs.length + '건 / 전체 ' + state.recs.length + '건 (Total수량 ' + qty(recs).toLocaleString() + ')</b></span>' +
      '<span class="legend"><i></i>파란색 머리글(✎) 칸은 클릭해서 엑셀처럼 직접 입력 · Enter 저장 · Esc 취소</span>' +
      '<span class="legend2"><i></i>원본과 다른 입력값</span>' +
      '<span class="muted">빈 값(.)은 - 로 표시 · 원본 .txt 는 바뀌지 않음</span></div>' +
      (n ? '<div class="xl-pend" role="status">저장하지 않은 변경 <b>' + n + '건</b>이 이 화면에만 있습니다. [저장] 을 눌러야 다른 사람에게 보입니다.' + (embedded ? ' (미리보기는 서버 없이 이 화면에만 저장)' : '') + '</div>' : '') +
      '<div class="xl-scroll"><table class="excel"><thead><tr>' + head + '</tr></thead><tbody>' +
      (body || '<tr><td colspan="' + (COLS.length + 2) + '" class="muted empty-row">조건에 맞는 데이터가 없습니다.</td></tr>') + '</tbody></table></div></section>' +
      deletedSection();
  }
  function deletedSection() {
    var gone = state.data.deleted || [];
    if (!gone.length) return '';
    var rows = gone.map(function (r) {
      var on = !!state.pending.restore[r._key];
      return '<tr' + (on ? ' class="p-res"' : '') + '><td class="mono">' + cell(r.Test) + '</td><td class="mono">' + cell(r.List_no) + '</td><td>' + cell(r.Process) + '</td><td>' + cell(r.Engineer) +
        '</td><td>' + tags(r.Lot_ID) + '</td><td class="muted">' + esc(r._deleted_by || '') + '</td><td><button class="rbtn' + (on ? ' undo' : '') + '" data-restore="' + esc(r._key) + '">' + (on ? '되살리기 취소' : '되살리기') + '</button></td></tr>';
    }).join('');
    return '<details class="xdel"' + (state.delOpen ? ' open' : '') + '><summary>삭제된 행 ' + gone.length + '건 <span class="muted">· 되살릴 수 있습니다</span></summary>' +
      '<div class="tablebox"><table><thead><tr><th>Test</th><th>List_no</th><th>Process</th><th>담당</th><th>Lot_ID</th><th>삭제</th><th></th></tr></thead><tbody>' + rows + '</tbody></table></div></details>';
  }

  // ---------- 저장 전 변경 (내 화면에만) ----------
  // 칸 입력·행 추가·삭제·되살리기는 [저장] 을 누르기 전까지 이 PC 의 이 화면에만 있다.
  // [저장] 하면 한 번에 서버로 보내고, 서버는 전부 반영하거나 전부 반영하지 않는다.
  var COLUMNS_17 = ['Test', 'Sdm_no', 'List_no', 'Process', 'Test_in', 'Test_out', 'DUT', 'Total수량', 'Wafer_in', 'PKG_Line', 'PKG_in', 'Vehicle', 'Tester', 'Engineer', '비고', 'Lot_ID', 'Structure'];
  var ROW_FIELDS = COLUMNS_17.concat(['설비', 'Oven']); // 원본에 없고 이 프로그램에만 저장
  function emptyPending() { return { set: {}, add: [], del: {}, restore: {} }; }
  function loadPending() {
    if (embedded) return emptyPending();
    try {
      var p = JSON.parse(localStorage.getItem('emdash.pending') || 'null');
      if (p && p.set && p.add && p.del && p.restore) return p;
    } catch (e) { /* 읽기 실패 시 비운다 */ }
    return emptyPending();
  }
  function savePending() {
    if (embedded) return;
    try { localStorage.setItem('emdash.pending', JSON.stringify(state.pending)); } catch (e) { /* 저장 불가 환경 */ }
  }
  function pendCount() {
    var P = state.pending, n = P.add.length + Object.keys(P.del).length + Object.keys(P.restore).length;
    Object.keys(P.set).forEach(function (k) { n += Object.keys(P.set[k]).length; });
    return n;
  }
  function keyOf(r) { return R.keyOf(r, keyFields()); }
  function copy(r) { var m = {}; for (var k in r) if (Object.prototype.hasOwnProperty.call(r, k)) m[k] = r[k]; return m; }

  // 서버 행 위에 저장 전 변경을 덮은 "화면용" 행 목록
  function displayRows() {
    var P = state.pending, out = [];
    function withSets(r) {
      var m = copy(r), s = P.set[r._key];
      m._pset = {}; m._pstale = {};
      if (s) Object.keys(s).forEach(function (f) {
        m._pset[f] = r[f] || '';
        m[f] = s[f].value;
        if (((r._ver && r._ver[f]) || 0) !== s[f].version) m._pstale[f] = 1;
      });
      return m;
    }
    state.data.rows.forEach(function (r) {
      var m = withSets(r);
      if (P.del[r._key]) m._pdel = true;
      out.push(m);
    });
    (state.data.deleted || []).forEach(function (r) {
      if (!P.restore[r._key]) return;
      var m = withSets(r); m._prestore = true; out.push(m);
    });
    P.add.forEach(function (r, i) {
      var m = copy(r);
      m._key = keyOf(r); m._padd = true; m._pi = i; m._ver = {}; m._src = {}; m._by = {}; m._pset = {}; m._pstale = {}; m._dup = false; m._line = null;
      out.push(m);
    });
    return out;
  }
  function recompute() {
    state.recs = R.analyze(displayRows(), { today: state.today, keyFields: keyFields() });
    fillFilterOptions();
    render();
  }
  function changed() { savePending(); recompute(); }

  // 서버(store.py)와 같은 정리 규칙. 문제가 있으면 Error(한국어 메시지)
  function cleanText(v) {
    v = String(v === null || v === undefined ? '' : v).replace(/[\r\n]+/g, ' ').trim().replace(/,/g, '，');
    if (v === '.') v = '';
    if (v.length > 200) throw new Error('200자 이하로 입력하세요.');
    return v;
  }
  function cleanDate(v, f) {
    if (!v) return v;
    var m = /^(\d{4})[-\/.](\d{1,2})[-\/.](\d{1,2})$/.exec(v);
    if (!m) throw new Error(f + ': 날짜는 2026-11-27 형식으로 입력하세요.');
    var d = new Date(Date.UTC(+m[1], +m[2] - 1, +m[3]));
    if (d.getUTCMonth() !== +m[2] - 1 || d.getUTCDate() !== +m[3]) throw new Error(f + ': 없는 날짜입니다 (' + v + ')');
    return d.toISOString().slice(0, 10);
  }
  function cleanVal(f, v) { v = cleanText(v); return R.DATE_FIELDS.indexOf(f) >= 0 ? cleanDate(v, f) : v; }
  function cleanRow(src) {
    var out = {};
    ROW_FIELDS.forEach(function (c) { out[c] = cleanVal(c, src[c]); });
    out.Test = out.Test || 'EM';
    ['List_no'].concat(keyFields().filter(function (f) { return f !== 'Test' && f !== 'List_no'; })).forEach(function (f) {
      if (!out[f]) throw new Error(f + ' 는 꼭 입력해야 합니다.');
    });
    if (!out.Process) throw new Error('Process 는 꼭 입력해야 합니다.');
    keyFields().forEach(function (f) { if (out[f].indexOf('|') >= 0) throw new Error(f + ' 에는 | 를 쓸 수 없습니다 (행 구분 키).'); });
    if (!out['Total수량']) { var d = /^\s*(\d+)/.exec(out.DUT); if (d) out['Total수량'] = String(parseInt(d[1], 10) * 16); }
    if (out['Total수량'] && !/^\d+$/.test(out['Total수량'])) throw new Error('Total수량 은 숫자로 입력하세요.');
    return out;
  }

  // ---------- 셀 입력 ----------
  function findRec(key) {
    for (var i = 0; i < state.recs.length; i++) if (state.recs[i].row._key === key) return state.recs[i].row;
    return null;
  }
  function who() { var el = $('who'); return el ? el.value.trim() : ''; }
  function toast(msg, bad) {
    var t = $('toast');
    t.textContent = msg; t.className = 'toast' + (bad ? ' bad' : ''); t.hidden = false;
    clearTimeout(toast.h); toast.h = setTimeout(function () { t.hidden = true; }, bad ? 5000 : 2600);
  }
  function startEdit(td) {
    if (state.editing) return;
    var key = td.getAttribute('data-key'), f = td.getAttribute('data-f');
    var r = findRec(key); if (!r) return;
    var isDate = R.DATE_FIELDS.indexOf(f) >= 0;
    state.editing = { key: key, field: f };
    td.classList.add('editing');
    td.innerHTML = '<input class="cellin" type="' + (isDate ? 'date' : 'text') + '" maxlength="200" aria-label="' + esc(f) + '">';
    var inp = td.firstChild;
    inp.value = r[f] || '';
    inp.focus(); if (!isDate) inp.select();
    var done = false;
    function finish(keep) {
      if (done) return; done = true;
      state.editing = null;
      if (keep) setCell(r, f, inp.value);
      if (state.pendingReload) { state.pendingReload = false; reload(); } else renderView();
    }
    inp.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') { e.preventDefault(); finish(true); }
      else if (e.key === 'Escape') { e.preventDefault(); finish(false); }
    });
    inp.addEventListener('blur', function () { finish(true); });
  }
  function setCell(r, f, raw) {
    var v;
    try { v = cleanVal(f, raw); } catch (e) { toast(e.message, true); return; }
    var P = state.pending;
    if (r._padd) { P.add[r._pi][f] = v; return changed(); }
    var server = f in r._pset ? r._pset[f] : (r[f] || '');
    var ver = (r._ver && r._ver[f]) || 0;
    var s = P.set[r._key] || (P.set[r._key] = {});
    if (v === server) delete s[f];
    else s[f] = { value: v, version: f in s ? s[f].version : ver };
    if (!Object.keys(s).length) delete P.set[r._key];
    changed();
  }

  // ---------- 행 추가 · 삭제 · 되살리기 ----------
  function toggleDelete(key) {
    var r = findRec(key); if (!r) return;
    var P = state.pending;
    if (r._padd) { P.add.splice(r._pi, 1); toast('추가 예정이던 행을 뺐습니다.'); return changed(); }
    if (r._prestore) { delete P.restore[key]; return changed(); }
    if (P.del[key]) delete P.del[key];
    else { P.del[key] = true; toast('삭제 예정으로 표시했습니다. [저장] 을 눌러야 반영됩니다.'); }
    changed();
  }
  function toggleRestore(key) {
    var P = state.pending;
    if (P.restore[key]) delete P.restore[key]; else P.restore[key] = true;
    changed();
  }
  var ADD_FIELDS = [
    { k: 'Test', ph: 'EM' }, { k: 'List_no', req: 1 }, { k: 'Process', req: 1, list: 'Process' }, { k: 'Sdm_no' },
    { k: 'DUT', ph: '진행/계획 보드, 예: 8/8' }, { k: 'Total수량', ph: '비우면 DUT × 16' }, { k: 'Vehicle' }, { k: 'Tester', list: 'Tester', ph: '작업자 이니셜' }, { k: '설비', list: '설비', ph: 'Micro oven 설비명' }, { k: 'Oven', ph: 'Oven(DUT) 번호, 예: 1|2|3 또는 1-8' },
    { k: 'Engineer', list: 'Engineer' }, { k: 'Wafer_in', date: 1 }, { k: 'PKG_Line', date: 1 }, { k: 'PKG_in', date: 1 },
    { k: 'Test_in', date: 1 }, { k: 'Test_out', date: 1 }, { k: 'Lot_ID', ph: '여러 개는 | 로 구분', wide: 1 },
    { k: 'Structure', ph: '여러 개는 | 로 구분', wide: 1 }, { k: '비고', wide: 1 }
  ];
  function openAdd() {
    var m = $('modal');
    var lists = {};
    var fields = ADD_FIELDS.map(function (d) {
      var id = 'a-' + ROW_FIELDS.indexOf(d.k);
      var opts = d.list ? uniq(d.list) : [];
      if (d.k === '설비') Object.keys(state.data.config.ovens || {}).forEach(function (n) { if (opts.indexOf(n) < 0) opts.push(n); });
      if (d.list && !lists[d.list]) lists[d.list] = '<datalist id="dl-' + d.list + '">' + opts.sort().map(function (v) { return '<option value="' + esc(v) + '">'; }).join('') + '</datalist>';
      var req = d.req || (d.k !== 'Test' && keyFields().indexOf(d.k) >= 0);
      return '<label class="' + (d.wide ? 'wide' : '') + '" for="' + id + '"><span>' + esc(d.k) + (req ? ' <b class="req">*</b>' : '') + '</span>' +
        '<input id="' + id + '" data-k="' + esc(d.k) + '" type="' + (d.date ? 'date' : 'text') + '" maxlength="200"' + (d.ph ? ' placeholder="' + esc(d.ph) + '"' : '') +
        (d.list ? ' list="dl-' + d.list + '"' : '') + (req ? ' required' : '') + ' autocomplete="off"></label>';
    }).join('');
    m.innerHTML = '<form class="dlg wide" id="addform" role="dialog" aria-modal="true" aria-labelledby="add-t" novalidate>' +
      '<h2 id="add-t">행 추가</h2><p>입력한 행은 [저장] 을 누르기 전까지 내 화면에만 보입니다. 원본 .txt 에는 쓰지 않습니다.</p>' +
      '<div class="fgrid">' + fields + '</div>' + Object.keys(lists).map(function (k) { return lists[k]; }).join('') +
      '<p class="ferr" id="add-err" role="alert" hidden></p>' +
      '<div class="dlg-b"><button type="button" class="btn" id="add-cancel">취소</button><button type="submit" class="btn primary">추가 (저장 대기)</button></div></form>';
    m.hidden = false;
    $('a-2').focus();
    $('add-cancel').onclick = function () { m.hidden = true; };
    $('addform').onsubmit = function (e) {
      e.preventDefault();
      var src = {};
      Array.prototype.forEach.call(m.querySelectorAll('input[data-k]'), function (i) { src[i.getAttribute('data-k')] = i.value; });
      var row, err = $('add-err');
      try {
        row = cleanRow(src);
        var k = keyOf(row);
        if (state.recs.some(function (x) { return x.row._key === k; })) throw new Error('이미 있는 행입니다 (' + keyFields().join('+') + ' = ' + keyLabel(k) + ')');
        var gone = (state.data.deleted || []).filter(function (r) { return r._key === k; })[0];
        if (gone && !gone._added) throw new Error('삭제된 행에 같은 행(' + keyLabel(k) + ')이 있습니다. 표 아래 "삭제된 행" 에서 되살리기를 쓰세요.');
      } catch (ex) { err.textContent = ex.message; err.hidden = false; return; }
      state.pending.add.push(row);
      m.hidden = true;
      state.f.stage = ''; // 새 행이 필터에 가려지지 않게
      changed();
      toast('List ' + row.List_no + ' 을 추가 예정으로 넣었습니다. [저장] 을 눌러야 반영됩니다.');
    };
  }

  // ---------- 저장 ----------
  function buildOps() {
    var P = state.pending, ops = [], refs = [];
    Object.keys(P.restore).forEach(function (k) { ops.push({ op: 'restore', key: k }); refs.push({ t: 'restore', key: k }); });
    P.add.forEach(function (r, i) {
      var row = {}; ROW_FIELDS.forEach(function (c) { row[c] = r[c] || ''; });
      ops.push({ op: 'add', row: row }); refs.push({ t: 'add', i: i, key: keyOf(r) });
    });
    Object.keys(P.set).forEach(function (k) {
      Object.keys(P.set[k]).forEach(function (f) {
        ops.push({ op: 'set', key: k, field: f, value: P.set[k][f].value, version: P.set[k][f].version });
        refs.push({ t: 'set', key: k, field: f });
      });
    });
    Object.keys(P.del).forEach(function (k) { ops.push({ op: 'delete', key: k }); refs.push({ t: 'delete', key: k }); });
    return { ops: ops, refs: refs };
  }
  function describe(ref) {
    var list = keyLabel(ref.key);
    return { restore: '되살리기', add: '행 추가', set: ref.field + ' 입력', delete: '행 삭제' }[ref.t] + ' · ' + list;
  }
  function dropRef(ref) {
    var P = state.pending;
    if (ref.t === 'restore') delete P.restore[ref.key];
    else if (ref.t === 'delete') delete P.del[ref.key];
    else if (ref.t === 'add') P.add = P.add.filter(function (r) { return keyOf(r) !== ref.key; });
    else if (P.set[ref.key]) { delete P.set[ref.key][ref.field]; if (!Object.keys(P.set[ref.key]).length) delete P.set[ref.key]; }
  }
  function save() {
    if (state.saving || !pendCount()) return;
    if (!who()) { toast('작업자 이름을 먼저 적어 주세요 (오른쪽 위).', true); $('who').focus(); return; }
    store('who', who());
    var b = buildOps();
    if (embedded) return commitLocal(b);
    state.saving = true; renderView();
    fetch('/api/save', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ user: who(), ops: b.ops }) })
      .then(function (r) { return r.json().then(function (j) { return { status: r.status, body: j }; }); })
      .then(function (res) {
        state.saving = false;
        if (res.status === 200) {
          state.pending = emptyPending(); savePending();
          toast('저장했습니다 (' + res.body.applied + '건 반영). 다른 PC 에도 곧 보입니다.');
          reload();
        } else if (res.status === 409) showConflicts(res.body.conflicts, b.refs);
        else showSaveError(res.body, b.refs);
      })
      .catch(function () { state.saving = false; renderView(); toast('서버에 저장하지 못했습니다. 연결을 확인하세요. 변경은 이 화면에 그대로 있습니다.', true); });
  }
  function showConflicts(list, refs) {
    var m = $('modal');
    var rows = list.map(function (c) {
      return '<tr><td class="mono">' + esc(keyLabel(c.key)) + '</td><td>' + esc(c.field) + '</td><td class="mono">' + cell(c.current.value) +
        '<div class="muted small">' + esc((c.current.by || '원본') + ' ' + (c.current.at || '')) + '</div></td><td class="mono">' + cell(c.mine) + '</td></tr>';
    }).join('');
    m.innerHTML = '<div class="dlg wide" role="dialog" aria-modal="true" aria-labelledby="dlg-t"><h2 id="dlg-t">다른 사람이 먼저 바꾼 칸이 ' + list.length + '개 있습니다</h2>' +
      '<p>아직 아무것도 저장하지 않았습니다. 어떻게 할지 고르세요.</p>' +
      '<div class="tablebox"><table class="cmp"><thead><tr><th>행</th><th>칸</th><th>지금 저장된 값</th><th>내가 입력한 값</th></tr></thead><tbody>' + rows + '</tbody></table></div>' +
      '<div class="dlg-b"><button class="btn" id="c-cancel">취소 (저장 안 함)</button><button class="btn" id="c-skip">겹친 칸은 저장된 값으로 두고 나머지 저장</button><button class="btn primary" id="c-over">모두 내 값으로 덮어쓰기</button></div></div>';
    m.hidden = false;
    $('c-skip').focus();
    $('c-cancel').onclick = function () { m.hidden = true; reload(); };
    $('c-skip').onclick = function () {
      m.hidden = true;
      list.forEach(function (c) { dropRef(refs[c.i]); });
      savePending();
      if (pendCount()) save(); else { toast('겹친 칸만 있어서 저장할 것이 없습니다.'); reload(); }
    };
    $('c-over').onclick = function () {
      m.hidden = true;
      list.forEach(function (c) { var s = state.pending.set[c.key]; if (s && s[c.field]) s[c.field].version = c.current.version; });
      savePending(); save();
    };
  }
  function showSaveError(body, refs) {
    var m = $('modal'), ref = body && body.i >= 0 ? refs[body.i] : null;
    m.innerHTML = '<div class="dlg" role="dialog" aria-modal="true" aria-labelledby="dlg-t"><h2 id="dlg-t">저장하지 못했습니다</h2>' +
      '<p>' + esc((body && body.error) || '알 수 없는 오류') + '</p>' + (ref ? '<p class="muted">문제가 된 변경: ' + esc(describe(ref)) + '</p>' : '') +
      '<p class="muted">아무것도 저장되지 않았고, 변경은 이 화면에 그대로 있습니다.</p>' +
      '<div class="dlg-b"><button class="btn" id="e-close">닫기</button>' + (ref ? '<button class="btn primary" id="e-drop">이 변경만 빼고 다시 저장</button>' : '') + '</div></div>';
    m.hidden = false;
    $('e-close').focus();
    $('e-close').onclick = function () { m.hidden = true; recompute(); };
    if (ref) $('e-drop').onclick = function () { m.hidden = true; dropRef(ref); savePending(); recompute(); save(); };
  }
  function discard() {
    var n = pendCount();
    if (!n || !window.confirm('저장하지 않은 변경 ' + n + '건을 모두 취소할까요?')) return;
    state.pending = emptyPending(); changed();
    toast('변경을 모두 취소했습니다.');
  }
  // 미리보기(서버 없음): 이 화면의 데이터에만 반영
  function commitLocal(b) {
    var d = state.data, at = new Date().toTimeString().slice(0, 8), u = who();
    function log(k, f, o, n) { state.localHistory.unshift({ at: at, user: u, key: k, field: f, old: o, new: n, ip: '미리보기' }); }
    function byKey(list, k) { for (var i = 0; i < list.length; i++) if (list[i]._key === k) return i; return -1; }
    b.ops.forEach(function (op) {
      var i, r;
      if (op.op === 'restore') { i = byKey(d.deleted, op.key); if (i >= 0) { r = d.deleted.splice(i, 1)[0]; delete r._deleted_by; d.rows.push(r); log(op.key, '(행 되살림)', '', '되살림'); } }
      else if (op.op === 'add') {
        r = op.row; r._key = keyOf(r); r._added = true; r._dup = false; r._ver = {}; r._src = {}; r._by = {}; r._line = null;
        d.rows.push(r); log(r._key, '(행 추가)', '', COLUMNS_17.map(function (c) { return r[c] || '.'; }).join(','));
      } else if (op.op === 'set') {
        i = byKey(d.rows, op.key); r = i >= 0 ? d.rows[i] : null;
        if (!r) return;
        var orig = op.field in r._src ? r._src[op.field] : (r[op.field] || '');
        log(op.key, op.field, r[op.field] || '', op.value);
        r[op.field] = op.value;
        if (op.value === orig) delete r._src[op.field]; else r._src[op.field] = orig;
        r._ver[op.field] = (r._ver[op.field] || 0) + 1;
        r._by[op.field] = u + ' (미리보기)';
      } else if (op.op === 'delete') {
        i = byKey(d.rows, op.key); if (i < 0) return;
        r = d.rows.splice(i, 1)[0]; r._deleted_by = u + ' ' + at; d.deleted.push(r); log(op.key, '(행 삭제)', '', '삭제');
      }
    });
    var n = b.ops.length;
    state.pending = emptyPending();
    apply(d);
    toast('미리보기에 저장했습니다 (' + n + '건). 실제 서버에는 저장되지 않습니다.');
  }

  function viewHistory() {
    var items = embedded ? state.localHistory : state.history;
    if (!embedded && !state.historyLoaded) {
      state.historyLoaded = true;
      fetch('/api/history', { cache: 'no-store' }).then(function (r) { return r.json(); }).then(function (j) {
        state.history = j.items; if (state.tab === 'history') renderView();
      });
      return '<p class="muted">불러오는 중...</p>';
    }
    if (!items || !items.length) return '<div class="panel"><h2>아직 입력 이력이 없습니다</h2><p class="desc">현황 표에서 칸 입력·행 추가·삭제 후 [저장] 하면 누가 언제 무엇을 바꿨는지 여기에 남습니다.</p></div>';
    return '<div class="tablebox"><table><thead><tr><th>시각</th><th>입력자</th><th>행</th><th>칸</th><th>이전 값</th><th>새 값</th><th>PC</th></tr></thead><tbody>' +
      items.map(function (h) {
        return '<tr><td class="mono">' + esc(h.at) + '</td><td>' + esc(h.user) + '</td><td class="mono">' + esc(keyLabel(h.key)) + '</td><td>' + esc(h.field) +
          '</td><td class="mono">' + cell(h.old) + '</td><td class="mono">' + cell(h.new) + '</td><td class="mono muted">' + esc(h.ip || '') + '</td></tr>';
      }).join('') + '</tbody></table></div><p class="foot">최근 300건. 서버의 data/overlay.db 에 영구 보관됩니다.</p>';
  }
  function downloadCsv() {
    var recs = filtered(false);
    var hdr = ['단계', '경과일', '도착일', 'Test', 'Sdm_no', 'List_no', 'Process', 'Test_in', 'Test_out', 'DUT', 'Total수량', 'Wafer_in', 'PKG_Line', 'PKG_in', 'Vehicle', 'Tester(작업자)', '설비', 'Oven(DUT)', 'Engineer', '비고', 'Lot_ID', 'Structure', '확인 필요'];
    var q = function (v) { v = v === null || v === undefined ? '' : String(v); return /[",\n]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v; };
    var lines = [hdr.join(',')].concat(recs.map(function (x) {
      var r = x.row;
      return [STAGE_LABEL[x.stage], x.aging === null ? '' : x.aging, x.since, r.Test, r.Sdm_no, r.List_no, r.Process, r.Test_in, r.Test_out, r.DUT, r['Total수량'], r.Wafer_in, r.PKG_Line, r.PKG_in, r.Vehicle, r.Tester, r['설비'], r.Oven, r.Engineer, r['비고'], r.Lot_ID, r.Structure, x.flags.join(' / ')].map(q).join(',');
    }));
    var blob = new Blob(['﻿' + lines.join('\r\n')], { type: 'text/csv;charset=utf-8' });
    var a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'EM_현황_' + fmtDay(state.today) + '.csv';
    document.body.appendChild(a); a.click(); a.remove();
  }

  function viewQuality() {
    var recs = filtered(true).filter(function (x) { return x.flags.length; });
    var q = state.data.quarantine;
    var a = recs.length ? '<div class="tablebox"><table><thead><tr><th class="n">원본 줄</th><th>List_no</th><th>Process</th><th>단계 판정</th><th>확인할 내용</th></tr></thead><tbody>' +
      recs.map(function (x) {
        return '<tr><td class="n mono">' + x.row._line + '</td><td class="mono">' + cell(x.row.List_no) + '</td><td>' + cell(x.row.Process) + '</td><td>' + pill(x.stage) + '</td><td class="wrap">' + x.flags.map(esc).join('<br>') + '</td></tr>';
      }).join('') + '</tbody></table></div>' : '<p class="muted">확인이 필요한 행이 없습니다.</p>';
    var b = q.length ? '<div class="tablebox"><table><thead><tr><th class="n">원본 줄</th><th>사유</th><th>원문</th></tr></thead><tbody>' +
      q.map(function (z) { return '<tr><td class="n mono">' + z.line + '</td><td>' + esc(z.reason) + '</td><td class="mono wrap">' + esc(z.raw) + '</td></tr>'; }).join('') + '</tbody></table></div>' : '<p class="muted">깨진 줄이 없습니다.</p>';
    return '<section class="panel"><h2>값을 확인해야 하는 행 ' + recs.length + '건</h2><p class="desc">단계 판정에는 그대로 들어갑니다. 날짜 형식 오류, 미래 날짜, 날짜 순서 역전, 선행 날짜 없음, 수량 불일치(DUT × 16), 중복 List_no 를 봅니다.</p>' + a + '</section>' +
      '<section class="panel"><h2>읽지 못한 줄 (격리) ' + q.length + '줄</h2><p class="desc">컬럼 수가 16 또는 17이 아닌 줄입니다. 집계에서 빠지며 원본 파일은 그대로입니다.</p>' + b + '</section>';
  }

  // ---------- 이벤트 ----------
  function goList(stage, extra) {
    state.f.stage = stage || '';
    if (extra) state.f[extra.k] = extra.v;
    state.tab = 'list';
    store('tab', state.tab);
    fillFilterOptions();
    render();
  }
  document.addEventListener('click', function (e) {
    var ec = e.target.closest('td.ed');
    if (ec && !ec.classList.contains('editing')) return startEdit(ec);
    var t = e.target.closest('[data-tab],[data-stage],[data-sort],[data-chip],[data-equip-none],[data-del],[data-restore],#csv,#reload,#reset,#addrow,#save,#discard');
    if (!t) return;
    if (t.hasAttribute('data-equip-none')) { state.f.equip = ''; state.f.q = ''; state.sort = { key: '설비', dir: 1 }; return goList('RUNNING'); }
    if (t.hasAttribute('data-del')) return toggleDelete(t.getAttribute('data-del'));
    if (t.hasAttribute('data-restore')) return toggleRestore(t.getAttribute('data-restore'));
    if (t.id === 'addrow') return openAdd();
    if (t.id === 'save') return save();
    if (t.id === 'discard') return discard();
    if (t.id === 'reload') return reload();
    if (t.id === 'csv') return downloadCsv();
    if (t.id === 'reset') {
      state.f = { process: '', tester: '', engineer: '', equip: '', q: '', stage: '' };
      $('f-q').value = ''; fillFilterOptions(); return render();
    }
    if (t.hasAttribute('data-tab')) { state.tab = t.getAttribute('data-tab'); store('tab', state.tab); return render(); }
    if (t.hasAttribute('data-chip')) { state.f.stage = t.getAttribute('data-chip'); return render(); }
    if (t.hasAttribute('data-sort')) {
      var k = t.getAttribute('data-sort');
      state.sort = state.sort.key === k ? { key: k, dir: -state.sort.dir } : { key: k, dir: k === 'aging' || k === 'flags' ? -1 : 1 };
      return render();
    }
    if (t.hasAttribute('data-x')) {
      var map = { Engineer: 'engineer', Process: 'process' };
      var v = t.getAttribute('data-v');
      return goList(t.getAttribute('data-stage'), { k: map[t.getAttribute('data-x')], v: v === '(미지정)' ? '' : v });
    }
    if (t.hasAttribute('data-stage')) return goList(t.getAttribute('data-stage'));
  });
  ['process', 'tester', 'engineer', 'equip'].forEach(function (k) {
    $('f-' + k).addEventListener('change', function (e) { state.f[k] = e.target.value; render(); });
  });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && e.target.matches && e.target.matches('td.ed')) { e.preventDefault(); startEdit(e.target); }
    if (e.key === 'Escape' && !$('modal').hidden) { $('modal').hidden = true; reload(); }
    if ((e.ctrlKey || e.metaKey) && (e.key === 's' || e.key === 'S') && $('modal').hidden) { e.preventDefault(); save(); }
  });
  document.addEventListener('toggle', function (e) { if (e.target.classList && e.target.classList.contains('xdel')) state.delOpen = e.target.open; }, true);
  window.addEventListener('beforeunload', function (e) {
    if (pendCount()) { e.preventDefault(); e.returnValue = '저장하지 않은 변경이 있습니다.'; return e.returnValue; }
  });
  window.addEventListener('storage', function (e) {
    // 같은 PC 의 다른 탭에서 바꾼 저장 전 변경을 맞춘다
    if (e.key === 'emdash.pending' && state.data) { state.pending = loadPending(); recompute(); }
  });
  var whoEl = $('who');
  whoEl.value = recall('who') || '';
  whoEl.addEventListener('change', function () { store('who', whoEl.value.trim()); });
  $('f-q').addEventListener('input', function (e) { state.f.q = e.target.value; render(); });

  var savedTab = recall('tab');
  if (savedTab && TABS.some(function (t) { return t.id === savedTab; })) state.tab = savedTab;
  if (embedded) $('reload').hidden = true;
  state.pending = loadPending();
  reload();
})();
