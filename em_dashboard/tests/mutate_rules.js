// 돌연변이 검사: rules.js 의 규칙을 한 줄씩 일부러 깨뜨려 test_rules.js 가 잡는지 본다.
// 하나라도 "살아남으면"(테스트 통과) 그 규칙은 테스트가 지키지 않는 것이다.
// node tests/mutate_rules.js
'use strict';
var fs = require('fs'), path = require('path'), os = require('os'), cp = require('child_process');
var src = fs.readFileSync(path.join(__dirname, '..', 'static', 'rules.js'), 'utf8');

var MUTANTS = [
  ['판정 순서: 진행중을 완료보다 먼저', "if (has(r.Test_out)) return 'DONE';\n    if (has(r.Test_in)) return 'RUNNING';", "if (has(r.Test_in)) return 'RUNNING';\n    if (has(r.Test_out)) return 'DONE';"],
  ['PKG 진행에서 PKG_Line 무시', "if (has(r.Wafer_in) || has(r.PKG_Line)) return 'PKG_LINE';", "if (has(r.Wafer_in)) return 'PKG_LINE';"],
  ['Test 대기 조건 제거', "if (has(r.PKG_in)) return 'TEST_WAIT';", ''],
  ['공백을 값으로 인정', "String(v).trim() !== ''", "String(v) !== ''"],
  ['체류 시작점: PKG_Line 대신 Wafer_in', "return has(r.PKG_Line) ? r.PKG_Line : r.Wafer_in;", 'return has(r.Wafer_in) ? r.Wafer_in : r.PKG_Line;'],
  ['진행중 시작점: PKG_in', "if (stage === 'RUNNING') return r.Test_in;", "if (stage === 'RUNNING') return r.PKG_in;"],
  ['입고대기 체류일 0으로 채움', "if (stage === 'DONE' || stage === 'WAFER_WAIT') return null;", "if (stage === 'DONE') return null; if (stage === 'WAFER_WAIT') return 0;"],
  ['미래 날짜 체류일 허용', 'return a < 0 ? null : a;', 'return a;'],
  ['지연 경계: 이상으로', 'return aging > lim;', 'return aging >= lim;'],
  // '지연: 체류일 null 검사 제거' 는 넣지 않는다: JS 에서 null > (0 이상) 이 false 라 결과가 같은 등가 돌연변이다.
  ['순서 역전 검사 제거', "if (prev && days[prev] > d) flags.push", 'if (false) flags.push'],
  ['빈 날짜에서 순서 검사 끊김', "if (d === null || isNaN(d)) return;\n      if (prev", "if (d === null || isNaN(d)) { prev = null; return; }\n      if (prev"],
  ['미래 날짜 검사 제거', "else if (d !== null && d > today) flags.push", 'else if (false) flags.push'],
  ['수량 기준 16 -> 8', 'var BOARD_DUT = 16;', 'var BOARD_DUT = 8;'],
  ['중복 검사 제거', 'seen[keyOf(r, kf)] > 1', 'seen[keyOf(r, kf)] > 99'],
  ['이번 주 = 8일', 'today - tin < 7', 'today - tin <= 7'],
  ['진행 보드에 대기 포함', "if (x.stage === 'RUNNING' && x.boards !== null)", "if ((x.stage === 'RUNNING' || x.stage === 'TEST_WAIT') && x.boards !== null)"],
  ['설비 부하에 완료 포함', "if (x.stage === 'RUNNING') {\n        map[t].running", "if (x.stage === 'RUNNING' || x.stage === 'DONE') {\n        map[t].running"],
  ['Oven 겹침 검사에 완료 포함', "if (x.stage !== 'RUNNING' || !has(x.row['설비'])) return;", "if (!has(x.row['설비'])) return;"],
  ['Oven 겹침을 설비 구분 없이', "var k = x.row['설비'] + '#' + n;", "var k = 'X#' + n;"],
  ['Oven 대수 검사 제거', 'ov.length !== boardsOf(r)', 'false'],
  ['Oven 범위 끝 빠짐', 'for (var i = a; i <= b; i++)', 'for (var i = a; i < b; i++)'],
  ['날짜 검증 제거(2월 30일 허용)', 'if (dt.getUTCFullYear() !== y || dt.getUTCMonth() !== mo - 1 || dt.getUTCDate() !== d) return NaN;', '']
];

var tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'mut-'));
var survived = 0, broken = 0;
MUTANTS.forEach(function (m, i) {
  if (src.indexOf(m[1]) < 0) { console.log('찾을 수 없음 (규칙 코드가 바뀜): ' + m[0]); broken++; return; }
  var f = path.join(tmp, 'rules_' + i + '.js');
  fs.writeFileSync(f, src.replace(m[1], m[2]));
  var r = cp.spawnSync(process.execPath, [path.join(__dirname, 'test_rules.js')], { env: Object.assign({}, process.env, { RULES_PATH: f }) });
  if (r.status === 0) { survived++; console.log('살아남음: ' + m[0]); }
  else console.log('잡힘     : ' + m[0]);
});
console.log((survived || broken ? 'FAILED' : 'OK') + ' 돌연변이 ' + MUTANTS.length + '개 중 잡힘 ' + (MUTANTS.length - survived - broken) + ', 살아남음 ' + survived + ', 찾을 수 없음 ' + broken);
process.exit(survived || broken ? 1 : (broken ? 2 : 0));
