/*
 * EM 평가 현황 - 판정 규칙 (단일 소재지)
 *
 * 화면의 모든 숫자(단계별 건수, 체류일, 지연, 데이터 확인 필요)는 이 파일의 함수에서만 나온다.
 * 규칙을 바꾸면 tests/test_rules.js 와 tests/mutate_rules.js 를 같이 돌린다.
 *
 * 단계 판정 (v1.2 규칙 그대로, "값이 있는가"만 본다. 형식이 틀린 날짜도 "있음"으로 본다):
 *   1. Test_out 있음                      -> DONE       평가완료
 *   2. Test_in 있음                       -> RUNNING    EM 진행중
 *   3. PKG_in 있음                        -> TEST_WAIT  Test 대기
 *   4. Wafer_in 또는 PKG_Line 있음         -> PKG_LINE   PKG 진행
 *   5. 그 외                              -> WAFER_WAIT 입고대기
 *
 * 체류일 = 기준일 - 그 단계에 들어온 날짜 (일)
 *   PKG_LINE: PKG_Line, 없으면 Wafer_in / TEST_WAIT: PKG_in / RUNNING: Test_in
 *   DONE, WAFER_WAIT 는 체류일을 내지 않는다 (null). 날짜가 깨졌거나 미래면 null. 0 으로 채우지 않는다.
 *
 * 지연 = 체류일이 단계별 기준일(config.ini [stage_limits])을 "초과"할 때. 기준이 없으면 지연 판정 안 함.
 */
(function (root) {
  'use strict';

  var STAGES = [
    { id: 'WAFER_WAIT', label: '입고대기' },
    { id: 'PKG_LINE', label: 'PKG 진행' },
    { id: 'TEST_WAIT', label: 'Test 대기' },
    { id: 'RUNNING', label: 'EM 진행중' },
    { id: 'DONE', label: '평가완료' }
  ];
  var DATE_FIELDS = ['Wafer_in', 'PKG_Line', 'PKG_in', 'Test_in', 'Test_out'];
  var BOARD_DUT = 16;

  function has(v) {
    return v !== null && v !== undefined && String(v).trim() !== '';
  }

  // 날짜 문자열 -> 일 번호(UTC 기준 정수). 빈 값 null, 형식 오류 NaN.
  function parseDate(s) {
    if (!has(s)) return null;
    var m = /^(\d{4})[-\/.](\d{1,2})[-\/.](\d{1,2})$/.exec(String(s).trim());
    if (!m) return NaN;
    var y = +m[1], mo = +m[2], d = +m[3];
    var dt = new Date(Date.UTC(y, mo - 1, d));
    if (dt.getUTCFullYear() !== y || dt.getUTCMonth() !== mo - 1 || dt.getUTCDate() !== d) return NaN;
    return Math.round(dt.getTime() / 864e5);
  }

  function dayNumber(dateObj) {
    return Math.round(Date.UTC(dateObj.getFullYear(), dateObj.getMonth(), dateObj.getDate()) / 864e5);
  }

  function stageOf(r) {
    if (has(r.Test_out)) return 'DONE';
    if (has(r.Test_in)) return 'RUNNING';
    if (has(r.PKG_in)) return 'TEST_WAIT';
    if (has(r.Wafer_in) || has(r.PKG_Line)) return 'PKG_LINE';
    return 'WAFER_WAIT';
  }

  function stageSince(r, stage) {
    if (stage === 'PKG_LINE') return has(r.PKG_Line) ? r.PKG_Line : r.Wafer_in;
    if (stage === 'TEST_WAIT') return r.PKG_in;
    if (stage === 'RUNNING') return r.Test_in;
    if (stage === 'DONE') return r.Test_out;
    return '';
  }

  function agingDays(r, stage, today) {
    if (stage === 'DONE' || stage === 'WAFER_WAIT') return null;
    var d = parseDate(stageSince(r, stage));
    if (d === null || isNaN(d)) return null;
    var a = today - d;
    return a < 0 ? null : a;
  }

  function isOverdue(aging, stage, limits) {
    var lim = limits ? limits[stage] : undefined;
    if (aging === null || lim === undefined || lim === null || isNaN(lim)) return false;
    return aging > lim;
  }

  function boardsOf(r) {
    var m = /^\s*(\d+)/.exec(String(r.DUT || ''));
    return m ? +m[1] : null;
  }

  // Oven(DUT) 번호 칸 해석: "1|2|3", "1-8", "1~4|7", "1 2 3" -> [1,2,3...] (정렬, 중복 제거)
  // 비었으면 [], 읽을 수 없는 조각이 있으면 null
  function ovenNumbers(v) {
    if (!has(v) || String(v).trim() === '.') return [];
    var out = {}, ok = true;
    String(v).split(/[|,，\/\s]+/).forEach(function (t) {
      if (!t) return;
      var m = /^(?:#|no\.?)?(\d+)(?:\s*[-~]\s*(\d+))?$/i.exec(t);
      if (!m) { ok = false; return; }
      var a = +m[1], b = m[2] !== undefined ? +m[2] : a;
      if (b < a || b - a > 99) { ok = false; return; }
      for (var i = a; i <= b; i++) out[i] = 1;
    });
    if (!ok) return null;
    return Object.keys(out).map(Number).sort(function (x, y) { return x - y; });
  }

  // 데이터 확인 필요 사유 목록. 단계 판정과는 독립이다.
  function flagsOf(r, today) {
    var flags = [];
    var days = {};
    DATE_FIELDS.forEach(function (f) {
      var d = parseDate(r[f]);
      days[f] = d;
      if (d !== null && isNaN(d)) flags.push('날짜 형식 오류: ' + f);
      else if (d !== null && d > today) flags.push('미래 날짜: ' + f);
    });
    var prev = null;
    DATE_FIELDS.forEach(function (f) {
      var d = days[f];
      if (d === null || isNaN(d)) return;
      if (prev && days[prev] > d) flags.push('날짜 순서 역전: ' + prev + ' > ' + f);
      prev = f;
    });
    if (has(r.Test_out) && !has(r.Test_in)) flags.push('Test_in 없이 Test_out');
    if (has(r.Test_in) && !has(r.PKG_in)) flags.push('PKG_in 없이 Test_in');
    var b = boardsOf(r);
    var q = has(r['Total수량']) ? Number(r['Total수량']) : null;
    if (b !== null && q !== null && !isNaN(q) && q !== b * BOARD_DUT) {
      flags.push('수량 불일치: DUT ' + b + ' x ' + BOARD_DUT + ' != ' + q);
    }
    return flags;
  }

  var DEFAULT_KEY = ['Test', 'List_no'];
  // 행 구분 키 (서버 store.row_key 와 같은 규칙). fields 는 config.ini [data] row_key
  function keyOf(r, fields) {
    return (fields || DEFAULT_KEY).map(function (f) { return String(r[f] || ''); }).join('|');
  }

  // rows: 파서가 돌려준 원본 행 / opts: { today: 일 번호, limits: {STAGE: 일} }
  function analyze(rows, opts) {
    var today = opts.today, limits = opts.limits || {};
    var seen = {};
    var kf = opts.keyFields || DEFAULT_KEY;
    var dupMsg = '중복 행 (' + kf.join('+') + ')';
    rows.forEach(function (r) { var k = keyOf(r, kf); seen[k] = (seen[k] || 0) + 1; });
    var recs = rows.map(function (r) {
      var stage = stageOf(r);
      var aging = agingDays(r, stage, today);
      var flags = flagsOf(r, today);
      if (has(r.List_no) && seen[keyOf(r, kf)] > 1) flags.push(dupMsg);
      var ov = ovenNumbers(r.Oven);
      if (ov === null) flags.push('Oven 번호 형식 확인: ' + r.Oven + ' (예: 1|2|3 또는 1-8)');
      else if (stage === 'RUNNING' && ov.length && boardsOf(r) !== null && ov.length !== boardsOf(r)) {
        flags.push('Oven ' + ov.length + '대 입력 ≠ 진행 보드 ' + boardsOf(r));
      }
      return {
        row: r,
        stage: stage,
        since: stageSince(r, stage) || '',
        aging: aging,
        overdue: isOverdue(aging, stage, limits),
        boards: boardsOf(r),
        ovens: ov || [],
        flags: flags
      };
    });
    // 같은 설비의 같은 Oven 을 진행중 두 건이 쓰면 양쪽에 표시
    var used = {};
    recs.forEach(function (x) {
      if (x.stage !== 'RUNNING' || !has(x.row['설비'])) return;
      x.ovens.forEach(function (n) { var k = x.row['설비'] + '#' + n; (used[k] = used[k] || []).push(x); });
    });
    Object.keys(used).forEach(function (k) {
      if (used[k].length < 2) return;
      used[k].forEach(function (x) {
        var others = used[k].filter(function (y) { return y !== x; }).map(function (y) { return y.row.List_no; });
        x.flags.push('Oven 겹침: ' + k.replace('#', ' Oven ') + ' (List ' + others.join(', ') + ')');
      });
    });
    return recs;
  }

  function summarize(recs, today) {
    var s = { total: recs.length, byStage: {}, overdueByStage: {}, inThisWeek: 0, outThisWeek: 0, runningBoards: 0, flagged: 0 };
    STAGES.forEach(function (st) { s.byStage[st.id] = 0; s.overdueByStage[st.id] = 0; });
    recs.forEach(function (x) {
      s.byStage[x.stage] += 1;
      if (x.overdue) s.overdueByStage[x.stage] += 1;
      var tin = parseDate(x.row.Test_in), tout = parseDate(x.row.Test_out);
      if (tin !== null && !isNaN(tin) && today - tin >= 0 && today - tin < 7) s.inThisWeek += 1;
      if (tout !== null && !isNaN(tout) && today - tout >= 0 && today - tout < 7) s.outThisWeek += 1;
      if (x.stage === 'RUNNING' && x.boards !== null) s.runningBoards += x.boards;
      if (x.flags.length) s.flagged += 1;
    });
    return s;
  }

  // 테스터별 부하: 진행중 보드 합 / 용량, 대기 건수
  // field: 묶는 칸 (기본 Tester, 화면은 작업자가 입력하는 '설비')
  function equipmentLoad(recs, capacity, field) {
    var map = {};
    recs.forEach(function (x) {
      var t = x.row[field || 'Tester'] || '(미지정)';
      if (!map[t]) map[t] = { tester: t, running: 0, boards: 0, waiting: 0, capacity: null };
      if (x.stage === 'RUNNING') {
        map[t].running += 1; map[t].boards += x.boards || 0;
        (x.ovens || []).forEach(function (n) { var o = map[t].occupied || (map[t].occupied = {}); (o[n] = o[n] || []).push(x.row.List_no); });
      }
      if (x.stage === 'TEST_WAIT') map[t].waiting += 1;
    });
    Object.keys(capacity || {}).forEach(function (t) {
      if (!map[t]) map[t] = { tester: t, running: 0, boards: 0, waiting: 0, capacity: null };
    });
    return Object.keys(map).sort().map(function (t) {
      var c = capacity && capacity[t] !== undefined ? Number(capacity[t]) : null;
      map[t].capacity = c !== null && !isNaN(c) && c > 0 ? c : null;
      return map[t];
    });
  }

  var api = {
    STAGES: STAGES, DATE_FIELDS: DATE_FIELDS, BOARD_DUT: BOARD_DUT,
    has: has, parseDate: parseDate, dayNumber: dayNumber, stageOf: stageOf, stageSince: stageSince,
    agingDays: agingDays, isOverdue: isOverdue, boardsOf: boardsOf, flagsOf: flagsOf,
    analyze: analyze, keyOf: keyOf, ovenNumbers: ovenNumbers, summarize: summarize, equipmentLoad: equipmentLoad
  };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.EMRules = api;
})(typeof window !== 'undefined' ? window : this);
