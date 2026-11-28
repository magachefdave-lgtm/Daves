// 판정 규칙 테스트. 의존성 없음: node tests/test_rules.js
// RULES_PATH 환경변수로 다른 rules.js(돌연변이 사본)를 시험할 수 있다.
'use strict';
var path = require('path');
var R = require(process.env.RULES_PATH || path.join(__dirname, '..', 'static', 'rules.js'));

var fails = 0, n = 0;
function eq(actual, expected, name) {
  n++;
  var a = JSON.stringify(actual), e = JSON.stringify(expected);
  if (a !== e) { fails++; console.log('FAIL ' + name + '\n  기대 ' + e + '\n  실제 ' + a); }
}
function row(o) {
  var base = { Test: 'EM', Sdm_no: '1', List_no: '1', Process: 'P', Test_in: '', Test_out: '', DUT: '8/8', 'Total수량': '128',
    Wafer_in: '', PKG_Line: '', PKG_in: '', Vehicle: 'v', Tester: 'T1', Engineer: 'E', '비고': '', Lot_ID: 'L', Structure: 'S' };
  for (var k in o) base[k] = o[k];
  return base;
}
var TODAY = R.parseDate('2026-11-27');

// [위험] 판정 순서가 바뀌면 진행중 건이 완료나 대기로 조용히 옮겨가 단계별 건수가 달라진다.
// 날짜 5개의 있음/없음 32개 조합 전부를 v1.2 우선순위 표로 고정한다.
(function () {
  var F = ['Wafer_in', 'PKG_Line', 'PKG_in', 'Test_in', 'Test_out'];
  for (var mask = 0; mask < 32; mask++) {
    var o = {};
    F.forEach(function (f, i) { o[f] = (mask >> i) & 1 ? '2026-11-01' : ''; });
    var has = function (f) { return o[f] !== ''; };
    var exp = has('Test_out') ? 'DONE' : has('Test_in') ? 'RUNNING' : has('PKG_in') ? 'TEST_WAIT'
      : (has('Wafer_in') || has('PKG_Line')) ? 'PKG_LINE' : 'WAFER_WAIT';
    eq(R.stageOf(row(o)), exp, 'stage mask ' + mask);
  }
})();
// 대표 사례는 값으로 못 박는다 (위 표가 같이 틀리는 경우 대비)
eq(R.stageOf(row({})), 'WAFER_WAIT', '날짜 없음 = 입고대기');
eq(R.stageOf(row({ Wafer_in: '2026-11-01' })), 'PKG_LINE', 'Wafer_in 만 = PKG 진행');
eq(R.stageOf(row({ PKG_Line: '2026-11-01' })), 'PKG_LINE', 'PKG_Line 만 = PKG 진행');
eq(R.stageOf(row({ Wafer_in: '2026-11-01', PKG_in: '2026-11-10' })), 'TEST_WAIT', 'PKG_in = Test 대기');
eq(R.stageOf(row({ PKG_in: '2026-11-10', Test_in: '2026-11-12' })), 'RUNNING', 'Test_in = 진행중');
eq(R.stageOf(row({ Test_out: '2026-11-20' })), 'DONE', 'Test_out 만 있어도 완료');
// [위험] 형식이 틀린 날짜를 "없음"으로 보면 진행중 건이 대기로 떨어진다. v1.2 는 값 존재만 본다.
eq(R.stageOf(row({ PKG_in: '2026-11-10', Test_in: '2026-13-02' })), 'RUNNING', '깨진 날짜도 있음');
eq(R.stageOf(row({ Test_in: '   ' })), 'WAFER_WAIT', '공백만 있으면 없음');

// 날짜 파싱
eq(R.parseDate('2026-11-27') - R.parseDate('2026-11-20'), 7, '날짜 차 7일');
eq(R.parseDate('2026/11/27'), R.parseDate('2026-11-27'), '슬래시 형식');
eq(R.parseDate('2026.11.27'), R.parseDate('2026-11-27'), '점 형식');
eq(isNaN(R.parseDate('2026-02-30')), true, '없는 날짜는 NaN');
eq(isNaN(R.parseDate('11/27/2026')), true, '미국식은 NaN');
eq(R.parseDate(''), null, '빈 값 null');
eq(R.parseDate('2024-03-01') - R.parseDate('2024-02-28'), 2, '윤년');

// [위험] 체류일 시작점을 잘못 잡으면 지연 목록 전체가 틀린다.
eq(R.agingDays(row({ Wafer_in: '2026-11-01', PKG_Line: '2026-11-07' }), 'PKG_LINE', TODAY), 20, 'PKG 진행은 PKG_Line 부터');
eq(R.agingDays(row({ Wafer_in: '2026-11-01' }), 'PKG_LINE', TODAY), 26, 'PKG_Line 없으면 Wafer_in 부터');
eq(R.agingDays(row({ PKG_in: '2026-11-20' }), 'TEST_WAIT', TODAY), 7, 'Test 대기는 PKG_in 부터');
eq(R.agingDays(row({ PKG_in: '2026-11-01', Test_in: '2026-11-25' }), 'RUNNING', TODAY), 2, '진행중은 Test_in 부터');
eq(R.agingDays(row({ Test_in: '2026-11-01', Test_out: '2026-11-20' }), 'DONE', TODAY), null, '완료는 체류일 없음');
eq(R.agingDays(row({}), 'WAFER_WAIT', TODAY), null, '입고대기는 체류일 없음 (0 아님)');
eq(R.agingDays(row({ Test_in: '2026-13-01' }), 'RUNNING', TODAY), null, '깨진 날짜는 체류일 없음');
eq(R.agingDays(row({ Test_in: '2026-12-01' }), 'RUNNING', TODAY), null, '미래 날짜는 체류일 없음');
eq(R.agingDays(row({ Test_in: '2026-11-27' }), 'RUNNING', TODAY), 0, '오늘 시작은 0일');

// [위험] 경계에서 기준일 "당일"을 지연으로 세면 지연 건수가 부풀려진다. 초과만 지연.
var L = { PKG_LINE: 21, TEST_WAIT: 7, RUNNING: 42 };
eq(R.isOverdue(7, 'TEST_WAIT', L), false, '기준일 당일은 지연 아님');
eq(R.isOverdue(8, 'TEST_WAIT', L), true, '기준일 +1 은 지연');
eq(R.isOverdue(100, 'DONE', L), false, '기준 없는 단계는 지연 아님');
eq(R.isOverdue(null, 'RUNNING', L), false, '체류일 없으면 지연 아님');
eq(R.isOverdue(43, 'RUNNING', L), true, '진행중 43일 지연');
eq(R.isOverdue(43, 'RUNNING', {}), false, '기준 미설정이면 지연 아님');

// 데이터 확인 플래그
eq(R.flagsOf(row({ Wafer_in: '2026-11-01', PKG_Line: '2026-11-05', PKG_in: '2026-11-10', Test_in: '2026-11-12' }), TODAY), [], '정상 행은 플래그 없음');
eq(R.flagsOf(row({ Test_out: '2026-11-20' }), TODAY), ['Test_in 없이 Test_out'], 'Test_in 없이 Test_out');
eq(R.flagsOf(row({ PKG_in: '2026-11-20', Test_in: '2026-11-15' }), TODAY), ['날짜 순서 역전: PKG_in > Test_in'], '순서 역전');
eq(R.flagsOf(row({ Test_in: '2026-11-15' }), TODAY), ['PKG_in 없이 Test_in'], 'PKG_in 없이 Test_in');
eq(R.flagsOf(row({ PKG_in: '2026-12-01' }), TODAY), ['미래 날짜: PKG_in'], '미래 날짜');
eq(R.flagsOf(row({ PKG_in: '2026-11-31' }), TODAY), ['날짜 형식 오류: PKG_in'], '형식 오류');
eq(R.flagsOf(row({ DUT: '8/8', 'Total수량': '120' }), TODAY), ['수량 불일치: DUT 8 x 16 != 120'], '수량 불일치');
eq(R.flagsOf(row({ DUT: '', 'Total수량': '120' }), TODAY), [], 'DUT 없으면 수량 검사 안 함');
// 중간 날짜가 비어도 앞뒤 순서는 본다
eq(R.flagsOf(row({ Wafer_in: '2026-11-20', PKG_in: '2026-11-10' }), TODAY), ['날짜 순서 역전: Wafer_in > PKG_in'], '빈 날짜 건너뛰고 순서 검사');

// analyze / summarize
var rows = [
  row({ List_no: '1' }),
  row({ List_no: '2', Wafer_in: '2026-10-01' }),                                   // PKG 57일, 지연
  row({ List_no: '3', PKG_in: '2026-11-25' }),                                     // 대기 2일
  row({ List_no: '4', PKG_in: '2026-11-01', Test_in: '2026-11-24', DUT: '6/6', 'Total수량': '96' }), // 진행 3일, 보드 6
  row({ List_no: '5', PKG_in: '2026-09-01', Test_in: '2026-10-01', DUT: '8/8' }), // 진행 57일 지연, 보드 8
  row({ List_no: '5', PKG_in: '2026-09-01', Test_in: '2026-10-01', Test_out: '2026-11-25' }) // 완료, 중복 행
];
var recs = R.analyze(rows, { today: TODAY, limits: L });
eq(recs.map(function (x) { return x.stage; }), ['WAFER_WAIT', 'PKG_LINE', 'TEST_WAIT', 'RUNNING', 'RUNNING', 'DONE'], 'analyze 단계');
eq(recs.map(function (x) { return x.overdue; }), [false, true, false, false, true, false], 'analyze 지연');
eq(recs[4].flags.indexOf('중복 행 (Test+List_no)') >= 0 && recs[5].flags.indexOf('중복 행 (Test+List_no)') >= 0, true, '중복 List_no 양쪽 표시');
eq(recs[0].flags, [], '중복 아닌 행은 플래그 없음');
var s = R.summarize(recs, TODAY);
eq(s.byStage, { WAFER_WAIT: 1, PKG_LINE: 1, TEST_WAIT: 1, RUNNING: 2, DONE: 1 }, '단계별 건수');
eq(s.overdueByStage, { WAFER_WAIT: 0, PKG_LINE: 1, TEST_WAIT: 0, RUNNING: 1, DONE: 0 }, '단계별 지연');
eq(s.runningBoards, 14, '진행 중 보드 합');
eq(s.inThisWeek, 1, '이번 주 투입 (최근 7일 Test_in)');
eq(s.outThisWeek, 1, '이번 주 완료');
eq(s.flagged, 2, '플래그 건수');

// [위험] '이번 주'를 8일로 세면 주간 보고 투입 건수가 부풀려진다. 오늘 포함 최근 7일.
var wk = R.summarize(R.analyze([
  row({ PKG_in: '2026-11-01', Test_in: '2026-11-20' }),  // 7일 전: 제외
  row({ PKG_in: '2026-11-01', Test_in: '2026-11-21' }),  // 6일 전: 포함
  row({ PKG_in: '2026-11-01', Test_in: '2026-11-27' })   // 오늘: 포함
], { today: TODAY, limits: L }), TODAY);
eq(wk.inThisWeek, 2, '이번 주 경계 (7일 전 제외)');

// 설비 부하
var eqp = R.equipmentLoad(R.analyze([
  row({ Tester: 'A', PKG_in: '2026-11-01', Test_in: '2026-11-20', DUT: '8/8' }),
  row({ Tester: 'A', PKG_in: '2026-11-01', Test_in: '2026-11-21', DUT: '4/4', 'Total수량': '64' }),
  row({ Tester: 'A', PKG_in: '2026-11-25' }),
  row({ Tester: 'B', Test_in: '2026-11-01', Test_out: '2026-11-20' })
], { today: TODAY, limits: L }), { A: 16, C: 8 });
eq(eqp, [
  { tester: 'A', running: 2, boards: 12, waiting: 1, capacity: 16 },
  { tester: 'B', running: 0, boards: 0, waiting: 0, capacity: null },
  { tester: 'C', running: 0, boards: 0, waiting: 0, capacity: 8 }
], '테스터별 부하 (완료 건 제외, 설정만 있는 테스터 포함)');

eq(R.equipmentLoad(R.analyze([
  row({ List_no: '1', Tester: 'JYJ', '설비': 'MO-01', DUT: '8/8', PKG_in: '2026-11-01', Test_in: '2026-11-02' }),
  row({ List_no: '2', Tester: 'KMS', '설비': 'MO-01', DUT: '4/8', PKG_in: '2026-11-01', Test_in: '2026-11-02' }),
  row({ List_no: '3', Tester: 'JYJ', DUT: '2/2', PKG_in: '2026-11-01', Test_in: '2026-11-02' })
], { today: TODAY }), { 'MO-01': 24 }, '설비').map(function (e) { return [e.tester, e.boards, e.capacity]; }),
  [['(미지정)', 2, null], ['MO-01', 12, 24]], '설비 칸 기준 Oven 사용 (진행 보드 = DUT 앞 숫자)');

// Oven(DUT) 번호 칸
eq(R.ovenNumbers('1|2|3'), [1, 2, 3], 'Oven | 구분');
eq(R.ovenNumbers('1-4|7'), [1, 2, 3, 4, 7], 'Oven 범위');
eq(R.ovenNumbers('3~1'), null, 'Oven 거꾸로 범위는 형식 오류');
eq(R.ovenNumbers('8 7 7'), [7, 8], 'Oven 공백 구분, 중복 제거');
eq(R.ovenNumbers('A1'), null, 'Oven 숫자 아님');
eq(R.ovenNumbers('.'), [], 'Oven 빈 값');
eq(R.ovenNumbers('1000000000'), null, 'Oven 큰 번호는 형식 오류');
eq(R.ovenNumbers('0'), null, 'Oven 0번 없음');
eq(R.ovenNumbers('99'), [99], 'Oven 상한 99');
var ovr = R.analyze([
  row({ List_no: '1', '설비': 'MO-01', Oven: '1-4', DUT: '4/4', PKG_in: '2026-11-01', Test_in: '2026-11-02' }),
  row({ List_no: '2', '설비': 'MO-01', Oven: '4|5', DUT: '4/8', PKG_in: '2026-11-01', Test_in: '2026-11-02' }),
  row({ List_no: '3', '설비': 'MO-02', Oven: '4', DUT: '1/1', PKG_in: '2026-11-01', Test_in: '2026-11-02' }),
  row({ List_no: '4', '설비': 'MO-01', Oven: '1', DUT: '1/1', PKG_in: '2026-11-01', Test_in: '2026-10-02', Test_out: '2026-11-01' })
], { today: TODAY });
eq(ovr[0].flags.filter(function (f) { return /Oven/.test(f); }), ['Oven 겹침: MO-01 Oven 4 (List 2)'], '같은 설비 같은 Oven 겹침');
eq(ovr[1].flags.filter(function (f) { return /Oven/.test(f); }), ['Oven 2대 입력 ≠ 진행 보드 4', 'Oven 겹침: MO-01 Oven 4 (List 1)'], '진행 보드 수와 다름 + 겹침');
eq(ovr[2].flags.filter(function (f) { return /Oven/.test(f); }), [], '다른 설비 같은 번호는 정상');
eq(ovr[3].flags.filter(function (f) { return /Oven/.test(f); }), [], '완료 건은 겹침 검사 제외');
eq(R.equipmentLoad(ovr, { 'MO-01': 8 }, '설비')[0].occupied, { 1: ['1'], 2: ['1'], 3: ['1'], 4: ['1', '2'], 5: ['2'] }, '설비별 사용 중 Oven 번호');

console.log((fails ? 'FAILED ' : 'OK ') + (n - fails) + '/' + n);
process.exit(fails ? 1 : 0);
