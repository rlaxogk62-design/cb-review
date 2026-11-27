/* DART 복합금융상품 리뷰 대시보드 — 화면 로직
 * 흐름: 회사 검색 → 회차 선택 → 공시 자동 입력(+근거) → 주가·금리 → 평가 → 엑셀 내보내기
 * 근거 형광펜: 원문(iframe)의 글자를 공백을 뺀 색인으로 만들고, '항목명' 다음에 나오는 '값'을 찾아 <mark>로 감싼다.
 */
'use strict';

const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const fmt = (v, d = 0) => (v == null || v === '' || isNaN(v) ? '' : Number(v).toLocaleString('ko-KR', { maximumFractionDigits: d, minimumFractionDigits: d }));
const fmtMil = (v) => (v == null || isNaN(v) ? '' : (v / 1e6).toLocaleString('ko-KR', { maximumFractionDigits: 1, minimumFractionDigits: 1 }));
const num = (s) => { if (s == null) return null; const t = String(s).replace(/[,\s%원]/g, ''); if (t === '' || t === '-') return null; const n = Number(t); return isNaN(n) ? null : n; };

const st = {
  corp: null, issue: null, fields: {}, alerts: [], summary: null, history: [], disc: {},
  market: { S: null, sigma: null, source: '' }, result: null, currentDoc: null, loading: false,
};

/* ------------------------------------------------------------ 입력 항목 정의
 * [키, 화면 이름, 형식, Temp 열]
 * 형식: text · date · money(콤마) · num · pct(%로 보여주고 소수로 저장) · ratio · sel:a|b · sched(일정표) */
const GROUPS = [
  { k: '2', title: '사채 조건', rows: [
    ['회차', '회차', 'text', 'F'], ['발행일', '발행일', 'date', 'AF'], ['만기일', '만기일', 'date', 'AG'],
    ['발행총액', '발행총액(원)', 'money', 'AE'], ['잔여총액', '잔여총액(원)', 'money', 'AB'],
    ['표면이자율', '표면이자율', 'pct', 'AH'], ['연간이자지급횟수', '연 이자지급 횟수', 'num', 'AK'],
    ['만기이자율', '만기보장수익률', 'pct', 'AJ'], ['상환활증방식', '상환할증 방식', 'sel:연복리|분복리|단리|고정율', 'AL'],
    ['RedemR', '만기상환율(고정율)', 'ratio', 'AM'], ['사모여부', '발행 방법', 'sel:사모|공모', '할인율'],
  ] },
  { k: '3', title: '전환 조건', rows: [
    ['최초전환가격', '최초 전환가격', 'money', 'AO'], ['현조정행사가격', '현 조정 전환가격', 'money', 'AN'],
    ['전환초일', '전환청구 시작일', 'date', 'AQ'], ['전환말일', '전환청구 종료일', 'date', 'AR'],
    ['상하향조정', '리픽싱(상하향)', 'sel:일반|하|상하|공모가조정|old하|old상하', 'AT'], ['Refix최소', '리픽싱 하한(원)', 'num', 'AU'],
    ['전환가격조정시작일', '첫 조정일', 'date', 'AP'], ['전환가조정연회수', '조정 연 횟수', 'num', 'AS'],
    ['주식총수대비', '희석(주식총수 대비)', 'pct', '참고'],
  ] },
  { k: '4', title: '조기상환(Put) · 매도청구(Call)', rows: [
    ['Put초일', 'Put 시작일', 'date', 'AX'], ['Put말일', 'Put 종료일', 'date', 'AY'], ['Put연회수', 'Put 연 횟수', 'num', 'BB'],
    ['YTP', '조기상환수익률', 'pct', 'AZ'], ['YTP복리', 'YTP 복리', 'sel:연복리|분복리|단리|고정율', 'BA'], ['Put일정', 'Put 일정표', 'sched', 'BK~CN'],
    ['Call초일', 'Call 시작일', 'date', 'BC'], ['Call말일', 'Call 종료일', 'date', 'BD'], ['Call연회수', 'Call 연 횟수', 'num', 'BG'],
    ['YTC', '매도청구 이율', 'pct', 'BE'], ['YTC복리', 'YTC 복리', 'sel:연복리|분복리|단리|고정율', 'BF'], ['Call한도', 'Call 한도', 'pct', 'BH'],
    ['Call순위', 'Call 순위', 'sel:선순위|후순위', 'BI'], ['Call3자배정', '제3자 지정', 'sel:Y|N', 'BJ'], ['Call일정', 'Call 일정표', 'sched', 'CO~DR'],
  ] },
];
// 종류주식(RCPS·CPS) — Temp RCPS 실무 입력례: 같은 DW 열, 만기 = 존속기간 만료일, 만기보장R = 상환 이율,
// Put초일(AV) = MAX(계약Put초일 AX, 배당가능초일 AW)
const RCPS_GROUPS = [
  { k: '2', title: '우선주 조건', rows: [
    ['회차', '회차', 'text', 'F'], ['발행일', '발행일', 'date', 'AF'], ['만기일', '존속기간 만료일', 'date', 'AG'],
    ['발행총액', '발행총액(원)', 'money', 'AE'], ['잔여총액', '잔여총액(원)', 'money', 'AB'],
    ['주당발행가', '주당 발행가(원)', 'money', 'AD'], ['발행주식수', '발행주식수', 'num', 'AC'], ['액면가', '액면가(원)', 'money', 'AI'],
    ['표면이자율', '표면R (우선배당)', 'pct', 'AH'], ['만기이자율', '상환 이율 (만기보장R)', 'pct', 'AJ'],
    ['상환활증방식', '상환 복리 방식', 'sel:연복리|분복리|단리|고정율', 'AL'], ['사모여부', '발행 방법', 'sel:사모|공모', '할인율'],
  ] },
  { k: '3', title: '전환 조건', rows: [
    ['최초전환가격', '최초 전환가격', 'money', 'AO'], ['현조정행사가격', '현 조정 전환가격', 'money', 'AN'],
    ['전환초일', '전환청구 시작일', 'date', 'AQ'], ['전환말일', '전환청구 종료일', 'date', 'AR'],
    ['상하향조정', '리픽싱(상하향)', 'sel:일반|하|상하|공모가조정|old하|old상하', 'AT'], ['공모가하한', 'IPO 공모가 기준', 'pct', 'AU'],
    ['Refix최소', '리픽싱 하한(원, 시가조정)', 'num', 'AU'],
  ] },
  { k: '4', title: '상환청구(Put) · 회사 상환(Call)', rows: [
    ['Put초일', '계약 상환청구 시작일', 'date', 'AX'], ['배당가능초일', '배당가능초일 (상환재원)', 'date', 'AW'],
    ['Put말일', '상환청구 종료일', 'date', 'AY'], ['Put연회수', '상환청구 연 횟수', 'num', 'BB'],
    ['YTP', '상환 이율 (YTP)', 'pct', 'AZ'], ['YTP복리', 'YTP 복리', 'sel:연복리|분복리|단리|고정율', 'BA'],
    ['Call초일', 'Call 시작일', 'date', 'BC'], ['Call말일', 'Call 종료일', 'date', 'BD'], ['Call연회수', 'Call 연 횟수', 'num', 'BG'],
    ['YTC', 'Call 이율', 'pct', 'BE'], ['YTC복리', 'YTC 복리', 'sel:연복리|분복리|단리|고정율', 'BF'], ['Call한도', 'Call 한도', 'pct', 'BH'],
    ['Call순위', 'Call 순위', 'sel:선순위|후순위', 'BI'], ['Call3자배정', '제3자 지정', 'sel:Y|N', 'BJ'],
    ['Call일정', 'Call 일정표', 'sched', 'CO~DR'],
  ] },
];
// 풋·콜옵션 (주식 대상) — Temp SO 시트 방식: 행사기간 안 조기행사, 할인 Rf 또는 Rd(SO후속), 행사가에 이율이 붙을 수 있음
const OPT_GROUPS = [
  { k: '2', title: '풋·콜옵션 조건 — 계약서(주주간 계약·투자계약)로 확인', rows: [
    ['회차', '옵션명', 'text', 'F'], ['옵션종류', '옵션 종류 (권리자 기준)', 'sel:Put|Call', 'E'],
    ['수량', '대상 주식수', 'num', 'AC'], ['행사가격', '행사가격 (주당, 원)', 'money', 'AN'],
    ['행사가증가율', '행사가 이율 (연)', 'pct', 'AJ'], ['행사가복리', '이율 방식', 'sel:연복리|분복리|단리', 'AL'],
    ['행사가기산일', '이율 기산일 (투자일)', 'date', 'AF'],
    ['행사시작일', '행사 시작일', 'date', 'AQ'], ['행사종료일', '행사 종료일 (만기)', 'date', 'AR'],
    ['할인', '할인 곡선', 'sel:Rf|Rd', '참고'], ['권리자', '권리자', 'text', '참고'], ['의무자', '의무자', 'text', '참고'],
  ] },
];
// 주식선택권 — Temp SO 시트: 행사기간(AQ~AR) 안에서만 조기행사하는 미국형 콜
const SO_GROUPS = [
  { k: '2', title: '주식선택권 조건', rows: [
    ['회차', '차수', 'text', 'F'], ['부여일', '부여일 (측정일)', 'date', 'AF'], ['행사가격', '행사가격(원)', 'money', 'AN'],
    ['행사시작일', '행사 시작일 (가득 후)', 'date', 'AQ'], ['행사종료일', '행사 종료일 (만기)', 'date', 'AR'],
    ['부여수량', '부여수량 (최초)', 'num', '참고'], ['잔여수량', '기말 미행사 수량', 'num', 'AC'],
    ['가득조건', '가득조건', 'text', '참고'], ['결제방식', '결제방식', 'sel:주식결제형|현금결제형', '참고'],
    ['기대배당', '기대배당수익률', 'pct', '참고'],
  ] },
  { k: '3', title: '회사 평가 가정 (주석) — 재계산 기본값', rows: [
    ['회사S', '기초자산 가격(원)', 'money', 'DS'], ['회사변동성', '기대주가변동성', 'pct', 'DT'], ['회사Rf', '무위험이자율', 'pct', 'DV'],
    ['회사만기', '옵션만기', 'date', '참고'], ['회사FV', '공정가치 (1개당, 원)', 'money', '대조'],
  ] },
];
const groupsFor = (kind) => (kind === 'SO' ? SO_GROUPS : kind === 'OPT' ? OPT_GROUPS : (kind === 'RCPS' || kind === 'CPS') ? RCPS_GROUPS : GROUPS);
const DATE_WORD = { SO: '부여', RCPS: '발행', CPS: '발행' };
const tmLabel = (it) => (it.kind === 'SO' || !/^\d+(-\d+)?$/.test(String(it.bd_tm || '')) ? String(it.bd_tm || '') : `제${it.bd_tm}회`);
const PRICE_WORD = { SO: '행사가', RCPS: '전환가', CPS: '전환가' };
const REPORT_KEYS = ['전체가치', '일반사채', 'Put', '전환권', 'Call'];
const REPORT_KEYS_SO = ['옵션1개당'];
// 곡선이 있는 등급 (2026-06-30 기준 확인: CCC0 이하·'B'처럼 0 없는 등급은 없음)
const GRADES = ['AAA', 'AA+', 'AA0', 'AA-', 'A+', 'A0', 'A-', 'BBB+', 'BBB0', 'BBB-', 'BB+', 'BB0', 'BB-', 'B+', 'B0', 'B-', 'CCC+'];

function fillGradeOptions(suggested) {
  const sel = $('#credit_grade');
  const cur = sel.value;
  const opt = (code) => `<option value="${code}">${code}${code === suggested ? '  ← 나이스 추천' : ''}</option>`;
  sel.innerHTML = '<option value="">(선택)</option>'
    + `<optgroup label="사모무보증 (등급 그대로)">${GRADES.map(opt).join('')}</optgroup>`
    + `<optgroup label="공모무보증 (공모부+등급)">${GRADES.map((g) => opt('공모부' + g)).join('')}</optgroup>`;
  if (cur) setGrade(cur);
}

function setGrade(code) {
  // 목록에 없는 등급(CCC0 이하 등)은 곡선이 있는 가장 낮은 등급(CCC+)으로 맞춘다
  if (!code) return;
  const pub = code.startsWith('공모부');
  const g = pub ? code.slice(3) : code;
  const use = GRADES.includes(g) ? code : (pub ? '공모부CCC+' : 'CCC+');
  $('#credit_grade').value = use;
  return use !== code;
}
const KIND_WORD = { CB: '전환', EB: '교환', BW: '행사', RCPS: '전환', CPS: '전환', SO: '행사', OPT: '행사' };

/* ------------------------------------------------------------ 공통 */
function toast(msg, err = false, ms = 3200) {
  const t = $('#toast');
  t.textContent = msg; t.className = 'toast' + (err ? ' err' : '');
  clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.add('hidden'), ms);
}
async function api(path, opts) {
  const r = await fetch(path, opts);
  const ct = r.headers.get('content-type') || '';
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).error || msg; } catch (e) { /* 무시 */ }
    throw new Error(msg);
  }
  return ct.includes('application/json') ? r.json() : r;
}
function busy(on, btn) { st.loading = on; if (btn) btn.disabled = on; document.body.style.cursor = on ? 'progress' : ''; }

/* ------------------------------------------------------------ 1. 검색 */
async function doSearch() {
  const q = $('#q').value.trim();
  if (!q) return;
  try {
    const list = await api('/api/search?q=' + encodeURIComponent(q));
    const box = $('#corpList');
    box.innerHTML = list.length ? '' : '<span class="muted">검색 결과가 없습니다.</span>';
    list.forEach((c) => {
      const b = document.createElement('button');
      b.className = 'chip'; b.innerHTML = `${esc(c.corp_name)}<small>${esc(c.stock_code || '비상장')}</small>`;
      b.onclick = () => { [...box.children].forEach((x) => x.classList.remove('on')); b.classList.add('on'); pickCorp(c); };
      box.appendChild(b);
    });
    if (list.length === 1) box.firstChild.click();
  } catch (e) { toast(e.message, true); }
}

async function pickCorp(c) {
  st.corp = c;
  const box = $('#issueList');
  box.innerHTML = '<span class="muted">발행결정 공시와 정기보고서 주석(RCPS·SO)을 불러오는 중…</span>';
  try {
    const list = await api(`/api/issues?corp_code=${c.corp_code}&val_date=${$('#val_date').value}`);
    st.issues = list;
    box.innerHTML = list.length ? '' : '<span class="muted">CB·BW·EB 발행결정 공시도, 정기보고서(사업·분기·감사보고서) 주석의 RCPS·SO도 찾지 못했습니다.</span>';
    list.forEach((it) => {
      const d = document.createElement('div');
      d.className = 'issue';
      const so = it.kind === 'SO', opt = it.kind === 'OPT';
      d.innerHTML = `<div class="k">${esc(opt ? '풋·콜' : it.kind)} · ${esc(tmLabel(it))}</div>
        <div class="t">${opt ? (it.key === 'MANUAL' ? '계약서 조건 입력' : '주석 약정 문장') : so ? esc(it.bd_fta) : esc(fmt(num(it.bd_fta))) + '원'}</div>
        ${opt ? '' : `<div class="d">${DATE_WORD[it.kind] || '결정'} ${esc(it.bddd || '')} · ${PRICE_WORD[it.kind] || KIND_WORD[it.kind] + '가'} ${esc(it.price || '-')} · 만기 ${esc(it.mtd || '-')}</div>`}
        ${it.src ? `<div class="d src">${esc(it.src)}</div>` : ''}`;
      d.onclick = () => { [...box.children].forEach((x) => x.classList.remove('on')); d.classList.add('on'); loadIssue(it); };
      box.appendChild(d);
    });
  } catch (e) { box.innerHTML = ''; toast(e.message, true); }
}

/* ------------------------------------------------------------ 2. 회차 불러오기 */
async function loadIssue(it, keepManual = false) {
  st.issue = it;
  const vd = $('#val_date').value;
  toast('공시 원문·조정 이력·재무 정보를 읽는 중… (처음엔 20~40초)', false, 60000);
  busy(true);
  try {
    const extra = it.note ? `&kind=${it.kind}&key=${encodeURIComponent(it.key || '')}` : '';
    const r = await api(`/api/load?corp_code=${st.corp.corp_code}&rcept_no=${it.rcept_no}&val_date=${vd}${extra}`);
    const prev = st.fields;
    const prevKind = st.loadedKind;
    st.loadedKind = it.kind;
    st.report = r.report || null;
    st.fields = r.fields;
    if (keepManual && prevKind === it.kind) for (const [k, v] of Object.entries(prev)) if (v && v.manual) st.fields[k] = v;
    st.alerts = r.alerts.map((a) => ({ ...a, checked: a.level !== 'info' || a.cat === '회차' }));
    st.summary = r.summary; st.history = r.history;
    st.disc = {}; (r.disclosures || []).forEach((d) => { st.disc[d.rcept_no] = d; });
    st.disc[it.rcept_no] = st.disc[it.rcept_no]
      || { report_nm: it.note ? (it.src || '정기보고서') : `${it.kind} 발행결정 (제${it.bd_tm}회)`, date: it.bddd };
    st.result = null;
    $('#work').classList.remove('hidden');
    $('#resultCard').classList.add('hidden');
    if (!keepManual) st.assume = {};
    st.related = r.related || [];
    renderFields(); renderAlerts(); renderReport(); renderAssume(); renderRelated();
    if (!keepManual) $('#placement').value = (st.fields['사모여부'] || {}).value === '공모' ? '0' : '1';
    applyKindLayout(it, prevKind);
    await showDoc(it.rcept_no);
    $('#docTitle').textContent = it.note
      ? `${st.corp.corp_name} ${it.kind} ${tmLabel(it)} — ${it.src || '정기보고서 주석'}`
      : `${st.corp.corp_name} ${it.kind} 제${it.bd_tm}회 발행결정 원문`;
    (r.warnings || []).forEach((w) => toast(w, true, 6000));
    if (!(r.warnings || []).length) toast('공시 자동 입력 완료. 주가·등급·할인율을 불러오는 중…', false, 5000);
    st.seen = new Set(Object.keys(st.disc));
    startWatch();
  } catch (e) { toast(e.message, true, 8000); busy(false); return false; }
  busy(false);
  await autoMarketData();
  return true;
}

/* ------------------------------------------------------------ 상품별 화면 (CB·RCPS·SO) */
const isSO = () => st.issue && st.issue.kind === 'SO';
const isOpt = () => st.issue && st.issue.kind === 'OPT';
const optLike = () => isSO() || isOpt();
const unlisted = () => !st.corp || !st.corp.stock_code;
function measureDate() {
  // 주식결제형 SO는 부여일에 측정 (재측정 없음), 그 밖에는 평가기준일
  if (isSO() && (st.fields['결제방식'] || {}).value !== '현금결제형') return (st.fields['부여일'] || {}).value || $('#val_date').value;
  return $('#val_date').value;
}
function applyKindLayout(it, prevKind) {
  const so = it.kind === 'SO';
  $('#peerBox').classList.toggle('hidden', !unlisted());
  $('#soNote').classList.toggle('hidden', !(so || it.kind === 'OPT'));
  if (it.kind === 'OPT') {
    $('#soNote').textContent = '풋·콜옵션: 행사가·이율·기산일·행사기간·수량을 계약서로 넣으세요. Temp SO 시트처럼 Put은 Rd, Call은 Rf로 할인합니다 (SO후속 — 할인 칸에서 바꿀 수 있음). '
      + '비상장이면 평가기준일 주가·변동성을 직접 넣거나 유사기업으로 계산하세요.';
  }
  if (so) {
    $('#soNote').textContent = `측정일 ${measureDate()} — ${((st.fields['결제방식'] || {}).value || '주식결제형')}. `
      + '주가·변동성·Rf 칸에는 주석의 회사 가정이 들어가 있습니다 (회사 공정가치 재현용). 고쳐서 민감도를 볼 수 있습니다.';
    // 곡선은 CB용으로 기억해 둔 값을 보관해 두고 SO 가정을 넣는다
    if (prevKind !== 'SO') st.savedCurves = { rf: $('#rf').value, rd: $('#rd').value };
    const f = (k) => (st.fields[k] || {}).value;
    if (f('회사S') != null) $('#S').value = f('회사S');
    if (f('회사변동성') != null) $('#sigma').value = (+f('회사변동성')).toFixed(4);
    if (f('회사Rf') != null) { $('#rf').value = String(+(f('회사Rf') * 100).toFixed(4)); $('#rd').value = ''; }
    st.market = { S: num($('#S').value), sigma: num($('#sigma').value), source: '주석(회사 평가 가정)' };
    $('#mktSrc').textContent = '주석(회사 평가 가정)';
    $('#marketNote').textContent = '주식선택권: 주석의 기초자산 가격·변동성을 넣었습니다. 상장사는 [주가·변동성 다시 불러오기]로 부여일 주가를 받을 수 있습니다.';
    curveCount(false);
  } else if (prevKind === 'SO' && st.savedCurves) {
    $('#rf').value = st.savedCurves.rf; $('#rd').value = st.savedCurves.rd; curveCount(false);
  }
}

/* ------------------------------------------------------------ 평가기준일 기준 시장자료 자동 수집 */
async function autoMarketData() {
  if (isSO()) {                                   // SO: 회사 가정을 이미 넣었다. 상장사이고 주가가 비었으면 부여일 주가를 받는다
    if (!unlisted() && !num($('#S').value)) await loadMarket();
    toast('주식선택권 조건과 회사 평가 가정을 채웠습니다. [평가 계산]으로 회사 공정가치와 대조하세요.');
    return;
  }
  await Promise.allSettled([loadMarket(), loadCredit()]);
  await loadCurves();
  toast('자동 입력 완료. 입력칸을 누르면 원문 근거가 형광펜으로 표시됩니다.');
}

async function loadCredit() {
  const box = $('#niceBox');
  if (window.CBR_WEB) {
    box.innerHTML = '<span class="muted">외부 사이트에서는 나이스 등급을 자동 조회하지 않습니다. 곡선 등급코드를 고르고, 아래 Rf·Rd에 Temp DW의 DV~EK · EM~EZ 값을 붙여넣으세요 (붙여넣은 값은 이 브라우저에 기억됩니다).</span>';
    return;
  }
  box.innerHTML = '<span class="muted">나이스 신용등급 조회 중…</span>';
  try {
    const priv = $('#placement').value;
    const r = await api(`/api/credit?corp_code=${st.corp.corp_code}&company=${encodeURIComponent(st.corp.corp_name)}&val_date=${$('#val_date').value}&private=${priv}`);
    st.credit = r; st.city = r.city;
    if (r.collecting) {
      box.innerHTML = `<span class="warnc">${esc(r.message)}</span>`;
      return;
    }
    const asof = r.asof || {};
    box.innerHTML = `<div class="muted">나이스 기업신용등급 (법인 RAS 서버 · 소재 ${esc(r.city || '-')}) — 평가기준일 이전 최신 등급을 곡선에 씁니다</div>
      <div class="scroll"><table><tr><th>평가일자</th><th>등급</th><th>기업명</th></tr>
      ${r.rows.map((x) => `<tr class="${x === r.rows.find((y) => y['평가일자'] === asof['평가일자'] && y['신용등급'] === asof['신용등급']) ? 'asof' : ''}">
        <td>${esc(x['평가일자'])}</td><td>${esc(x['신용등급'])}</td><td>${esc(x['기업명실제'] || x['기업명'])}</td></tr>`).join('')}</table></div>`;
    fillGradeOptions(r.code);
    if (!st.gradeManual && r.code && setGrade(r.code)) {
      box.insertAdjacentHTML('beforeend', `<div class="warnc">${esc(r.code)} 곡선이 없어 곡선이 있는 가장 낮은 등급(CCC+)으로 맞췄습니다.</div>`);
    }
    if (!r.asof) box.insertAdjacentHTML('beforeend', '<div class="warnc">평가기준일 이전 등급이 없습니다. 곡선 등급코드를 직접 넣어 주세요.</div>');
    else {
      const age = (new Date($('#val_date').value) - new Date(r.asof['평가일자'])) / (365.25 * 864e5);
      if (age > 1.5) box.insertAdjacentHTML('beforeend', `<div class="warnc">평가기준일 기준 최신 등급이 ${age.toFixed(1)}년 전(${esc(r.asof['평가일자'])}) 것입니다. 재무 상태·다른 평가로 등급을 판단해 곡선 등급코드를 직접 정하세요 (KICPA 3.7).</div>`);
    }
  } catch (e) {
    box.innerHTML = `<span class="warnc">나이스 등급을 불러오지 못했습니다: ${esc(e.message)} — 법인 서버(사내망) 연결을 확인하거나 등급코드를 직접 넣으세요.</span>`;
  }
}

async function loadCurves() {
  const grade = $('#credit_grade').value.trim();
  const note = $('#curveNote');
  if (window.CBR_WEB) { note.textContent = '직접 붙여넣은 값을 3개월 이표 YTM으로 보고 zero 금리로 부트스트랩합니다.'; return; }
  note.textContent = '할인율 곡선 조회 중…';
  try {
    const r = await api(`/api/curves?val_date=${$('#val_date').value}&grade=${encodeURIComponent(grade)}`);
    $('#rf').value = r.rf.map((x) => (x == null ? '' : x)).join('\t');
    if (r.rd) $('#rd').value = r.rd.map((x) => (x == null ? '' : x)).join('\t');
    curveCount();
    note.textContent = `법인 RAS 서버 (엑셀 매크로와 같은 곡선) · 국고채 ${r.rf_date}` + (r.rd ? ` · ${grade} ${r.rd_date} (${(r.rd_query || {})['구분'] || ''})` : ' · 등급코드가 없어 Rd는 비워 두었습니다')
      + '. 3개월 이표 YTM으로 보고 zero 금리로 부트스트랩합니다.';
    markStale();
  } catch (e) {
    note.innerHTML = `<span class="warnc">곡선을 불러오지 못했습니다: ${esc(e.message)}</span> — 사외라면 Temp DW의 DV~EK·EM~EZ를 붙여넣으세요.`;
  }
}

/* ------------------------------------------------------------ 새 공시 감시 (DART는 접수 즉시 목록에 올라온다) */
function startWatch() {
  clearInterval(st.watch);
  $('#newBanner').classList.add('hidden');
  st.watch = setInterval(checkNew, 5 * 60 * 1000);
}
async function checkNew() {
  if (!st.corp || !st.seen) return;
  try {
    const list = await api(`/api/latest?corp_code=${st.corp.corp_code}`);
    const fresh = list.filter((d) => !st.seen.has(d.rcept_no));
    if (!fresh.length) return;
    fresh.forEach((d) => { st.disc[d.rcept_no] = d; });
    const b = $('#newBanner');
    b.innerHTML = `<b>새 공시 ${fresh.length}건</b><ul>${fresh.slice(0, 5).map((d) => `<li>${esc(d.date)} ${esc(d.report_nm)}</li>`).join('')}</ul>
      <button class="primary" id="btnReload">반영해서 다시 불러오기</button> <button id="btnDismiss">닫기</button>`;
    b.classList.remove('hidden');
    $('#btnReload').onclick = async () => { b.classList.add('hidden'); await loadIssue(st.issue, true); };
    $('#btnDismiss').onclick = () => { fresh.forEach((d) => st.seen.add(d.rcept_no)); b.classList.add('hidden'); };
    toast(`${st.corp.corp_name} 새 공시 ${fresh.length}건이 올라왔습니다.`, false, 8000);
  } catch (e) { /* 다음 주기에 다시 */ }
}

/* ------------------------------------------------------------ 3. 입력칸 */
function shown(type, v) {
  if (v == null) return '';
  if (type === 'pct') return +(Number(v) * 100).toFixed(4);
  if (type === 'money') return fmt(v, Number.isInteger(Number(v)) ? 0 : 2);
  return v;
}
function parsed(type, s) {
  if (type === 'pct') { const n = num(s); return n == null ? null : n / 100; }
  if (type === 'money' || type === 'num' || type === 'ratio') return num(s);
  return s === '' ? null : s;
}

function renderFields() {
  const host = $('#fieldCards');
  host.innerHTML = '';
  const word = KIND_WORD[st.issue.kind] || '전환';
  groupsFor(st.issue.kind).forEach((g) => {
    const card = document.createElement('div');
    card.className = 'card';
    card.innerHTML = `<div class="cardhead"><span class="kicker">${g.k}</span>${esc(g.title.replace('전환', word))}</div>`;
    g.rows.forEach(([key, label, type, col]) => {
      const f = st.fields[key] || { value: null, evidence: [], conf: 'none' };
      st.fields[key] = f;
      const row = document.createElement('div');
      row.className = 'frow'; row.dataset.key = key;
      const hasEv = (f.evidence || []).some((e) => e && e.rcept_no);
      let input;
      if (type === 'sched') {
        const list = f.value || [];
        input = `<div class="sched">${list.length ? list.map(([d, r]) => `<span>${esc(d)} <b>${(r * 100).toFixed(4)}%</b></span>`).join('') : '<span class="muted">공시 일정표 없음 (시작일·연 횟수로 계산)</span>'}</div>`;
      } else if (type.startsWith('sel:')) {
        const opts = type.slice(4).split('|');
        const v = f.value ?? '';
        input = `<select>${['', ...opts].map((o) => `<option ${o === v ? 'selected' : ''}>${esc(o)}</option>`).join('')}${opts.includes(v) || !v ? '' : `<option selected>${esc(v)}</option>`}</select>`;
      } else {
        const t = type === 'date' ? 'date' : 'text';
        input = `<input type="${t}" value="${esc(shown(type, f.value))}" ${type === 'pct' ? 'placeholder="%"' : ''}>`;
      }
      row.innerHTML = `<div class="fl">${esc(label.replace('전환', word))}<small>${esc(col)}</small></div>${input}
        <span class="dot ${f.manual ? 'manual' : (f.conf || 'none')}" title="${f.manual ? '직접 수정' : ({ high: '공시에서 확인', mid: '공시 + 계산·추정', low: '기본값 — 확인 필요', none: '값 없음' }[f.conf || 'none'])}"></span>
        <button class="evbtn" ${hasEv ? '' : 'disabled'}>근거</button>
        ${f.note ? `<div class="fn">${esc(f.note)}</div>` : ''}`;
      const ctl = row.querySelector('input,select');
      if (ctl) {
        if (f.manual) ctl.classList.add('manual');
        ctl.addEventListener('focus', () => showEvidence(key));
        ctl.addEventListener('change', () => {
          f.value = parsed(type, ctl.value); f.manual = true;
          ctl.classList.add('manual'); row.querySelector('.dot').className = 'dot manual';
          if (type === 'money' && f.value != null) ctl.value = shown(type, f.value);
          // 풋·콜: Temp SO 시트처럼 Put이면 Rd, Call이면 Rf로 할인 — 할인을 직접 고르지 않았으면 따라 바꾼다
          const d = st.fields['할인'];
          if (key === '옵션종류' && d && !d.manual) {
            d.value = f.value === 'Call' ? 'Rf' : 'Rd';
            const sel = document.querySelector('.frow[data-key="할인"] select');
            if (sel) sel.value = d.value;
          }
          markStale();
        });
      }
      row.querySelector('.evbtn').onclick = () => showEvidence(key);
      row.addEventListener('click', (e) => { if (e.target === row || e.target.closest('.fl,.sched')) showEvidence(key); });
      card.appendChild(row);
    });
    host.appendChild(card);
  });
}

function markStale() {
  if (st.result) { $('#resTable').classList.add('stale'); $('#resInfo').textContent = '입력이 바뀌었습니다. [평가 계산]을 다시 누르세요.'; }
  renderAssume();
}

/* ------------------------------------------------------------ 4. 원문과 형광펜 */
function showDoc(rcept_no) {
  return new Promise((resolve) => {
    const fr = $('#doc');
    const link = $('#docLink');
    link.href = `https://dart.fss.or.kr/dsaf001/main.do?rcpNo=${rcept_no}`;
    link.classList.remove('hidden');
    const d = st.disc[rcept_no];
    $('#docTitle').textContent = d ? `${d.report_nm} (${(d.date || '').replace(/^(\d{4})(\d{2})(\d{2})$/, '$1-$2-$3')})` : rcept_no;
    if (st.currentDoc === rcept_no && fr.contentDocument && fr.contentDocument.body) return resolve();
    fr.onload = () => { st.currentDoc = rcept_no; resolve(); };
    if (window.CBR_WEB) {          // 외부 사이트: 원문을 받아 srcdoc으로 넣는다 (iframe 주소로는 브라우저 안 파이썬에 닿지 않음)
      fetch('/api/doc?rcept_no=' + rcept_no).then((r) => r.text()).then((h) => { fr.srcdoc = h; })
        .catch((e) => { toast('원문을 불러오지 못했습니다: ' + e.message, true); resolve(); });
    } else fr.src = '/api/doc?rcept_no=' + rcept_no;
  });
}

function textIndex(doc) {
  const w = doc.createTreeWalker(doc.body, NodeFilter.SHOW_TEXT, null);
  const nodes = [], ni = [], off = [];
  let s = '';
  while (w.nextNode()) {
    const n = w.currentNode, t = n.nodeValue, id = nodes.length;
    nodes.push(n);
    for (let k = 0; k < t.length; k++) {
      if (/\s/.test(t[k])) continue;
      s += t[k]; ni.push(id); off.push(k);
    }
  }
  return { nodes, ni, off, s };
}
const squash = (s) => String(s ?? '').replace(/\s+/g, '');

function wrap(doc, ix, a, b, cls) {
  const spans = new Map();
  for (let i = a; i < b; i++) {
    const id = ix.ni[i];
    const cur = spans.get(id);
    if (!cur) spans.set(id, [ix.off[i], ix.off[i]]); else cur[1] = ix.off[i];
  }
  let first = null;
  [...spans.entries()].sort((x, y) => y[0] - x[0]).forEach(([id, [s, e]]) => {
    const node = ix.nodes[id];
    if (!node.parentNode) return;
    const r = doc.createRange();
    r.setStart(node, s); r.setEnd(node, e + 1);
    const m = doc.createElement('mark'); m.className = cls;
    try { r.surroundContents(m); first = m; } catch (err) { /* 겹침 무시 */ }
  });
  return first;
}

function clearMarks(doc) {
  doc.querySelectorAll('mark.ev, mark.lab').forEach((m) => {
    const p = m.parentNode;
    while (m.firstChild) p.insertBefore(m.firstChild, m);
    p.removeChild(m); p.normalize();
  });
}

function highlight(label, text) {
  const doc = $('#doc').contentDocument;
  if (!doc || !doc.body) return false;
  clearMarks(doc);
  const T = squash(text), L = squash(label);
  if (!T) return false;
  let ix = textIndex(doc);
  let hit = -1, labAt = -1;
  if (L) {
    for (let p = ix.s.indexOf(L); p >= 0; p = ix.s.indexOf(L, p + 1)) {
      const q = ix.s.indexOf(T, p + L.length);
      if (q >= 0 && q - p < 6000) { hit = q; labAt = p; break; }
    }
  }
  if (hit < 0) hit = ix.s.indexOf(T);
  let len = T.length;
  if (hit < 0 && T.length > 18) { hit = ix.s.indexOf(T.slice(0, 18)); len = 18; }
  if (hit < 0) return false;
  wrap(doc, ix, hit, hit + len, 'ev');
  if (labAt >= 0 && labAt + L.length <= hit) {
    // <mark>로 감싸도 글자 순서는 그대로라 같은 위치를 다시 쓸 수 있다 (노드만 새로 색인)
    ix = textIndex(doc);
    wrap(doc, ix, labAt, labAt + L.length, 'lab');
  }
  const target = doc.querySelector('mark.ev');
  if (target) {
    // scrollIntoView는 바깥 화면까지 움직이므로, 원문 창 안에서만 스크롤한다
    // 원문 창이 화면 밖으로 일부 나가 있을 수 있으니, '지금 보이는 부분'의 위쪽 1/3에 형광펜이 오게 한다
    const win = doc.defaultView;
    const fr = $('#doc').getBoundingClientRect();
    const visTop = Math.max(0, -fr.top), visBot = Math.min(fr.height, window.innerHeight - fr.top);
    const anchor = visBot > visTop + 80 ? visTop + (visBot - visTop) / 3 : win.innerHeight / 3;
    const y = target.getBoundingClientRect().top + win.scrollY - anchor;
    win.scrollTo({ top: Math.max(0, y), behavior: 'auto' });   // 스크립트를 막은 원문 창에서는 smooth가 동작하지 않는다
  }
  return !!target;
}

async function showEvidence(key) {
  document.querySelectorAll('.frow.active').forEach((r) => r.classList.remove('active'));
  const row = document.querySelector(`.frow[data-key="${CSS.escape(key)}"]`);
  if (row) row.classList.add('active');
  const f = st.fields[key] || {};
  const evs = (f.evidence || []).filter((e) => e && e.rcept_no);
  const box = $('#evBox');
  box.classList.remove('hidden');
  if (!evs.length) {
    box.innerHTML = `<b>${esc(key)}</b> · 공시 근거 없음 — ${esc(f.manual ? '직접 수정한 값' : '기본값·계산값')}${f.note ? `<div class="muted">${esc(f.note)}</div>` : ''}`;
    return;
  }
  const e = evs[0];
  await showDoc(e.rcept_no);
  const ok = highlight(e.label, e.text);
  const d = st.disc[e.rcept_no] || {};
  box.innerHTML = `<b>${esc(key)}</b> = ${esc(Array.isArray(f.value) ? f.value.length + '회 일정' : f.value)} ·
    ${esc(d.report_nm || '공시')} · 항목 <b>${esc(e.label)}</b> · 문구 <span class="q">${esc(e.text)}</span>
    ${ok ? '' : '<div class="muted">원문에서 같은 문구를 찾지 못했습니다. DART에서 열어 확인하세요.</div>'}
    ${f.note ? `<div class="muted">${esc(f.note)}</div>` : ''}
    ${evs.length > 1 ? `<div class="muted">추가 근거 ${evs.length - 1}건: ${evs.slice(1).map((x) => esc(x.text)).join(' / ')}</div>` : ''}`;
}

/* ------------------------------------------------------------ 5. 유의 포인트 */
const ROLE_ORDER = ['발행조건', '가액조정', '잔액변동', 'Call', '약정', '지분', '조정사유', '신용', '시장', '주석', '참고'];
const ROLE_DESC = {
  발행조건: '발행 시점 조건 (전환가·Put·Call·리픽싱·상환)', 가액조정: '전환·행사가 조정 이력', 잔액변동: '전환·행사·취득으로 잔여 수량 변동',
  Call: 'Call(매수선택권) 지정·행사', 약정: '풋·콜·주주간 약정', 지분: '5% 보고·임원 소유 — CB 보유자, 콜·풋·담보 계약',
  조정사유: '병합·분할·무상증자·합병 → 전환가·주식수 조정', 신용: '부도·회생·자본잠식·손익 → 신용등급·Rd, 상환 가능 여부',
  시장: '거래정지·관리종목·상장폐지 → 주가·변동성 신뢰성, Put 사유', 주석: '정기보고서·감사보고서 주석', 참고: '그 밖에 확인할 공시',
};
function renderRelated() {
  const box = $('#related');
  const rel = st.related || [];
  $('#relCount').textContent = rel.length ? `${rel.length}건` : '';
  if (!rel.length) { box.innerHTML = '<span class="muted">관련 공시가 없습니다.</span>'; return; }
  const by = {};
  rel.forEach((d) => { (by[d.role] = by[d.role] || []).push(d); });
  box.innerHTML = ROLE_ORDER.filter((r) => by[r]).map((role) => `<div class="relgrp"><div class="relrole">${esc(role)} <small>${by[role].length}</small>
      <span class="muted">${esc(ROLE_DESC[role] || by[role][0].desc)}</span></div>
      ${by[role].slice(0, 12).map((d) => `<a class="relitem" data-rc="${esc(d.rcept_no)}" title="${esc(d.desc)}">${esc(d.date)} ${esc(d.report_nm)}</a>`).join('')}
      ${by[role].length > 12 ? `<div class="muted">외 ${by[role].length - 12}건</div>` : ''}</div>`).join('');
  box.querySelectorAll('.relitem').forEach((a) => a.addEventListener('click', async () => {
    const d = rel.find((x) => x.rcept_no === a.dataset.rc);
    st.disc[d.rcept_no] = st.disc[d.rcept_no] || { report_nm: d.report_nm, date: d.date };
    await showDoc(d.rcept_no);
    $('#evBox').classList.remove('hidden');
    $('#evBox').innerHTML = `<b>${esc(d.role)}</b> · ${esc(d.report_nm)} — ${esc(d.desc)}`;
  }));
}

function renderAlerts() {
  const box = $('#alerts');
  box.innerHTML = '';
  if (st.summary) {
    const s = st.summary;
    const eok = (v) => (v == null ? '-' : (v / 1e8).toLocaleString('ko-KR', { maximumFractionDigits: 0 }) + '억');
    const div = document.createElement('div');
    div.className = 'fin';
    div.innerHTML = `<div class="h">재무·감사 요약 <span class="muted">${esc(s.lab)}</span></div>
      <div class="fgrid">
        <span>자산 <b>${eok(s['자산총계'])}</b></span><span>부채 <b>${eok(s['부채총계'])}</b></span><span>자본 <b>${eok(s['자본총계'])}</b></span>
        <span>매출 <b>${eok(s['매출액'])}</b></span><span>영업이익 <b class="${(s['영업이익'] || 0) < 0 ? 'neg' : ''}">${eok(s['영업이익'])}</b></span>
        <span>순이익 <b class="${(s['당기순이익'] || 0) < 0 ? 'neg' : ''}">${eok(s['당기순이익'])}</b></span>
      </div>
      <div class="muted">감사인 ${esc(s['감사인'] || '-')} · ${esc(s['감사의견'] || '-')} · 강조사항 ${esc(s['강조사항'] || '-')}${s['핵심감사사항'] ? ' · KAM ' + esc(s['핵심감사사항']) : ''}</div>`;
    box.appendChild(div);
  }
  st.alerts.forEach((a, i) => {
    const d = document.createElement('div');
    d.className = 'alert ' + a.level;
    d.innerHTML = `<div class="h"><span class="cat">${esc(a.cat)}</span>${esc(a.title)}
        <label class="inc" title="리뷰 엑셀 '질의 및 추가확인 사항'에 넣기"><input type="checkbox" ${a.checked ? 'checked' : ''}> 리뷰</label></div>
      <div>${esc(a.why)}</div>
      ${a.items.length ? `<ul>${a.items.map((it) => `<li><a data-rc="${esc(it.rcept_no)}">${esc(it.date)} ${esc(it.report_nm)}</a></li>`).join('')}</ul>` : ''}`;
    d.querySelector('input').onchange = (e) => { st.alerts[i].checked = e.target.checked; };
    d.querySelectorAll('a[data-rc]').forEach((el) => {
      el.onclick = async () => {
        const rc = el.dataset.rc;
        if (!st.disc[rc]) st.disc[rc] = { report_nm: el.textContent };
        await showDoc(rc);
        $('#evBox').classList.remove('hidden');
        $('#evBox').innerHTML = `<b>공시 원문</b> · ${esc(el.textContent)}`;
      };
    });
    box.appendChild(d);
  });
  const bad = st.alerts.filter((a) => a.level === 'bad').length, warn = st.alerts.filter((a) => a.level === 'warn').length;
  $('#alertCount').textContent = `위험 ${bad} · 주의 ${warn} · 참고 ${st.alerts.length - bad - warn}`;
}

/* ------------------------------------------------------------ 6. 기초자산·금리·평가사 값 */
function renderReport() {
  const g = $('#reportGrid');
  const keys = optLike() ? REPORT_KEYS_SO : REPORT_KEYS;
  if (g.dataset.kind === keys.join()) return;
  g.dataset.kind = keys.join();
  g.innerHTML = '';
  keys.forEach((k) => {
    const l = document.createElement('label');
    const lab = k === '옵션1개당' ? (isOpt() ? '공정가치 주당 (원, 평가사)' : '공정가치 1개당 (원, 회사·평가사)') : k;
    l.innerHTML = `${lab}<input data-rep="${k}" placeholder="${k === 'Call' ? '음수로 입력' : '원'}">`;
    l.querySelector('input').addEventListener('change', (e) => { const v = num(e.target.value); e.target.value = v == null ? '' : fmt(v, 2).replace(/\.00$/, ''); markStale(); });
    g.appendChild(l);
  });
  if (isSO()) {                                   // 회사가 주석에 쓴 1개당 공정가치를 대조값으로 넣어 둔다
    const fvc = (st.fields['회사FV'] || {}).value;
    if (fvc != null) g.querySelector('[data-rep="옵션1개당"]').value = fmt(fvc, 2).replace(/\.00$/, '');
  }
}
/* ------------------------------------------------------------ 평가보고서 가정 ↔ 공시 대조
 * [키, 화면 이름, 형식, 대시보드 값 가져오기]  형식: money(원, 0.5% 이내 일치) · pct(%p, 0.01%p 이내) · date · sel:… · text */
const fv = (k) => (st.fields[k] || {}).value;
const ASSUME = [
  ['잔여총액', '잔여총액(원)', 'money', () => fv('잔여총액')],
  ['현조정행사가격', '현 전환가(원)', 'money', () => fv('현조정행사가격')],
  ['최초전환가격', '최초 전환가(원)', 'money', () => fv('최초전환가격')],
  ['만기일', '만기일', 'date', () => fv('만기일')],
  ['표면이자율', '표면이자율(%)', 'pct', () => fv('표면이자율')],
  ['만기이자율', '만기보장수익률(%)', 'pct', () => fv('만기이자율')],
  ['Put반영', 'Put 반영', 'sel:Y|N', () => (fv('Put초일') ? 'Y' : 'N')],
  ['YTP', '조기상환수익률(%)', 'pct', () => fv('YTP')],
  ['Call반영', 'Call 반영', 'sel:Y|N', () => (fv('Call초일') ? 'Y' : 'N')],
  ['Call한도', 'Call 한도(%)', 'pct', () => fv('Call한도')],
  ['상하향조정', '리픽싱', 'sel:일반|하|상하', () => fv('상하향조정')],
  ['Refix최소', '리픽싱 하한(원)', 'money', () => fv('Refix최소')],
  ['주가', '평가기준일 주가(원)', 'money', () => num($('#S').value)],
  ['변동성', '변동성(%)', 'pct', () => num($('#sigma').value)],
  ['등급', '할인율 곡선 등급', 'text', () => $('#credit_grade').value || null],
];
const ASSUME_RCPS = [
  ...ASSUME.slice(0, 5),
  ['배당가능초일', '배당가능초일 (상환재원)', 'date', () => fv('배당가능초일')],
  ['Put초일', '상환청구 시작일', 'date', () => fv('Put초일')],
  ...ASSUME.slice(5),
];
const ASSUME_SO = [
  ['부여일', '부여일 (측정일)', 'date', () => fv('부여일')],
  ['행사가격', '행사가격(원)', 'money', () => fv('행사가격')],
  ['행사시작일', '행사 시작일', 'date', () => fv('행사시작일')],
  ['행사종료일', '행사 종료일 (만기)', 'date', () => fv('행사종료일')],
  ['잔여수량', '수량', 'money', () => fv('잔여수량')],
  ['주가', '기초자산 가격(원)', 'money', () => num($('#S').value)],
  ['변동성', '변동성(%)', 'pct', () => num($('#sigma').value)],
  ['Rf', '무위험이자율(%)', 'pct', () => { const c = curve('#rf'); return c.length === 1 ? c[0] / 100 : null; }],
  ['기대배당', '기대배당수익률(%)', 'pct', () => fv('기대배당')],
];
const ASSUME_OPT = [
  ['옵션종류', '옵션 종류', 'sel:Put|Call', () => fv('옵션종류')],
  ['행사가격', '행사가격(원)', 'money', () => fv('행사가격')],
  ['행사가증가율', '행사가 이율(%)', 'pct', () => fv('행사가증가율')],
  ['행사시작일', '행사 시작일', 'date', () => fv('행사시작일')],
  ['행사종료일', '행사 종료일', 'date', () => fv('행사종료일')],
  ['수량', '대상 주식수', 'money', () => fv('수량')],
  ['주가', '주가(원)', 'money', () => num($('#S').value)],
  ['변동성', '변동성(%)', 'pct', () => num($('#sigma').value)],
  ['할인', '할인 곡선', 'sel:Rf|Rd', () => fv('할인')],
];
const assumeList = () => (isSO() ? ASSUME_SO : isOpt() ? ASSUME_OPT : (st.issue && ['RCPS', 'CPS'].includes(st.issue.kind)) ? ASSUME_RCPS : ASSUME);
st.assume = st.assume || {};
function assumeShow(type, v) {
  if (v == null || v === '') return '';
  if (type === 'pct') return (Number(v) * 100).toFixed(4).replace(/\.?0+$/, '') + '%';
  if (type === 'money') return fmt(v, Number.isInteger(Number(v)) ? 0 : 2);
  return String(v);
}
function assumeJudge(type, ours, theirs) {
  if (theirs == null || theirs === '') return { mark: '', diff: '' };
  if (ours == null || ours === '') return { mark: '?', diff: '대시보드 값 없음' };
  if (type === 'money') {
    const d = theirs - ours, r = ours ? Math.abs(d / ours) : Math.abs(d);
    return r <= 0.005 ? { mark: '✓', diff: '' } : { mark: '✗', diff: `${d > 0 ? '+' : ''}${fmt(d)} (${(d / (ours || 1) * 100).toFixed(1)}%)` };
  }
  if (type === 'pct') {
    const d = theirs - ours;
    return Math.abs(d) <= 0.0001 ? { mark: '✓', diff: '' } : { mark: '✗', diff: `${d > 0 ? '+' : ''}${(d * 100).toFixed(2)}%p` };
  }
  return String(ours).trim() === String(theirs).trim() ? { mark: '✓', diff: '' } : { mark: '✗', diff: '다름' };
}
function assumeRows() {
  return assumeList().map(([k, label, type, get]) => {
    const ours = get(), theirs = st.assume[k];
    return { k, label, type, ours, theirs, ...assumeJudge(type, ours, theirs) };
  });
}
function renderAssume() {
  const t = $('#assumeTable');
  if (!t || !st.issue) return;
  const rows = assumeRows();
  t.innerHTML = '<tr><th>항목</th><th>공시·대시보드</th><th>평가보고서</th><th>판정</th></tr>' + rows.map((r) => {
    let input;
    const cur = r.theirs == null ? '' : (r.type === 'pct' ? +(r.theirs * 100).toFixed(6) : r.type === 'money' ? fmt(r.theirs, 2).replace(/\.00$/, '') : r.theirs);
    if (r.type.startsWith('sel:')) input = `<select data-as="${r.k}">${['', ...r.type.slice(4).split('|')].map((o) => `<option ${o === (r.theirs || '') ? 'selected' : ''}>${o}</option>`).join('')}</select>`;
    else input = `<input data-as="${r.k}" type="${r.type === 'date' ? 'date' : 'text'}" value="${esc(cur)}" placeholder="${r.type === 'pct' ? '%' : ''}">`;
    const cls = r.mark === '✗' ? 'big' : '';
    return `<tr><td>${esc(r.label)}</td><td>${esc(assumeShow(r.type, r.ours))}</td><td>${input}</td><td class="${cls}">${r.mark} ${esc(r.diff)}</td></tr>`;
  }).join('');
  t.querySelectorAll('[data-as]').forEach((el) => el.addEventListener('change', () => {
    const k = el.dataset.as, type = assumeList().find((a) => a[0] === k)[2];
    const v = el.value.trim();
    st.assume[k] = v === '' ? null : type === 'pct' ? num(v) / 100 : type === 'money' ? num(v) : v;
    renderAssume();
  }));
  const bad = rows.filter((r) => r.mark === '✗').length, done = rows.filter((r) => r.mark).length;
  $('#assumeCount').textContent = done ? `${done}개 비교 · 불일치 ${bad}개` : '';
  $('#assumeCount').className = 'muted' + (bad ? ' warnc' : '');
}
function assumePayload() {
  return assumeRows().filter((r) => r.theirs != null && r.theirs !== '')
    .map((r) => ({ 항목: r.label, 공시: assumeShow(r.type, r.ours), 평가보고서: assumeShow(r.type, r.theirs), 판정: r.mark, 차이: r.diff }));
}

function report() {
  const o = {};
  document.querySelectorAll('[data-rep]').forEach((i) => { const v = num(i.value); if (v != null) o[i.dataset.rep] = v; });
  return o;
}
const curve = (id) => $(id).value.split(/[\s,;]+/).map((x) => x.trim()).filter((x) => x !== '').map(Number).filter((x) => !isNaN(x));
function curveCount(save = true) {
  const rf = curve('#rf').length, rd = curve('#rd').length;
  $('#curveCount').textContent = `Rf ${rf}/16 · Rd ${rd}/14`;
  $('#curveCount').className = 'muted' + (rf === 16 && rd === 14 ? '' : ' warnc');
  if (save && !isSO()) localStorage.setItem('curves', JSON.stringify({ rf: $('#rf').value, rd: $('#rd').value, grade: $('#credit_grade').value }));
}

async function loadMarket() {
  if (unlisted()) {
    const hint = latestFinancingPrice();
    $('#marketNote').innerHTML = '비상장: 주가(1주 가치)는 평가사 가정·최근 투자 단가 등으로 직접 넣고, 변동성은 아래 유사기업 코드로 계산하세요 (Temp proxy1~5).'
      + (hint ? `<div class="muted">참고 — 최근 종류주식 발행가: ${esc(hint)}</div>` : '');
    $('#mktSrc').textContent = '비상장 — 직접 입력';
    st.market.source = '수동 입력';
    return;
  }
  const vd = measureDate();
  if (!vd) return toast('평가기준일을 먼저 넣어 주세요.', true);
  const btn = $('#btnMarket');
  btn.disabled = true;
  $('#marketNote').textContent = '주가·변동성 조회 중…';
  try {
    const mat = (st.fields['만기일'] || {}).value || '';
    const r = await api(`/api/market?stock_code=${st.corp.stock_code}&val_date=${vd}&market=${(st.issue && st.issue.corp_cls) || ''}`
      + `&maturity=${mat}&period=${encodeURIComponent($('#period').value)}&window=${$('#window').value || 180}&corp_code=${st.corp.corp_code}`);
    $('#S').value = r.price;
    st.mkt = r;
    st.market.source = r.source + (r.price_note ? ' · 병합·분할 역조정' : '');
    $('#mktSrc').textContent = r.source;
    applyVolMethod();
    markStale();
  } catch (e) { $('#marketNote').innerHTML = `<span class="warnc">주가를 불러오지 못했습니다: ${esc(e.message)}</span> — 직접 입력하세요.`; }
  btn.disabled = false;
}

function applyVolMethod() {
  const r = st.mkt;
  if (!r) return;
  const iqr = r.vol_iqr;
  const useIqr = $('#volMethod').value === 'iqr' && iqr;
  $('#sigma').value = (useIqr ? iqr.vol : r.vol).toFixed(4);
  const line = (on, txt) => `<div${on ? ' class="volon"' : ''}>${on ? '▶ ' : ''}${esc(txt)}</div>`;
  $('#marketNote').innerHTML = esc(`${r.price_date} 종가 ${fmt(r.price)}원`)
    + line(!useIqr, `Temp S_vol: ${(r.vol * 100).toFixed(2)}% = 일 ${(r.daily_vol * 100).toFixed(2)}% × √${r.n_year} (최근 ${r.n_returns}개 일간 로그수익률)`)
    + (iqr ? line(useIqr, `1년·이상치 조정: ${(iqr.vol * 100).toFixed(2)}% = 일 ${(iqr.daily * 100).toFixed(2)}% × √${iqr.annual} (1년 ${iqr.n}개 중 ${iqr.clipped}개를 ${(iqr.lo * 100).toFixed(2)}% ~ ${(iqr.hi * 100).toFixed(2)}%로 조정)`) : '')
    + (r.price_note ? `<div class="warnc">${esc(r.price_note)}</div>` : '')
    + (r.errors.length ? `<div class="muted">참고: ${esc(r.errors.join(' / '))}</div>` : '');
  st.market.volMethod = useIqr ? '1년·이상치 조정(IQR)' : 'Temp S_vol';
}


/* ------------------------------------------------------------ 비상장: 최근 투자 단가 · 유사기업 변동성 */
function latestFinancingPrice() {
  const pref = (st.issues || []).filter((x) => ['RCPS', 'CPS'].includes(x.kind) && x.bddd && x.bddd <= $('#val_date').value);
  if (!pref.length) return null;
  const it = pref.reduce((a, b) => (a.bddd >= b.bddd ? a : b));
  return `${it.price}원 (제${it.bd_tm}회, ${it.bddd} 발행 — 주당 발행가)`;
}

async function loadPeers() {
  const codes = $('#peers').value.trim();
  if (!codes) return toast('유사기업 종목코드를 넣어 주세요 (예: 048910, 020120).', true);
  const btn = $('#btnPeers');
  btn.disabled = true;
  $('#peerNote').textContent = '유사기업 주가·변동성 조회 중…';
  try {
    const mat = (st.fields['만기일'] || st.fields['행사종료일'] || {}).value || '';
    const r = await api(`/api/peers?codes=${encodeURIComponent(codes)}&val_date=${measureDate()}&period=${encodeURIComponent($('#period').value)}`
      + `&window=${$('#window').value || 180}&maturity=${mat}`);
    st.peers = r.rows.filter((x) => x.vol).map((x) => ({ code: x.code, name: x.name }));
    const useIqr = $('#volMethod').value === 'iqr' && r.avg_iqr;
    const avg = useIqr ? r.avg_iqr : r.avg;
    if (avg) {
      $('#sigma').value = avg.toFixed(4);
      st.market.source = `유사기업 ${r.n}개 평균`;
      st.market.volMethod = useIqr ? '1년·이상치 조정(IQR)' : 'Temp S_vol';
      $('#mktSrc').textContent = `유사기업 ${r.n}개 평균`;
    }
    $('#peerNote').innerHTML = `<table class="res"><tr><th>유사기업</th><th>코드</th><th>Temp S_vol</th><th>1년·IQR</th></tr>${r.rows.map((x) => (x.vol
      ? `<tr><td>${esc(x.name)}</td><td>${esc(x.code)}</td><td>${(x.vol * 100).toFixed(2)}%</td><td>${x.vol_iqr ? (x.vol_iqr * 100).toFixed(2) + '%' : '-'}</td></tr>`
      : `<tr><td>${esc(x.name)}</td><td>${esc(x.code)}</td><td colspan="2" class="warnc">${esc(x.error || '실패')}</td></tr>`)).join('')}
      <tr class="total"><td>평균 (${r.n}개)</td><td></td><td>${r.avg ? (r.avg * 100).toFixed(2) + '%' : '-'}</td><td>${r.avg_iqr ? (r.avg_iqr * 100).toFixed(2) + '%' : '-'}</td></tr></table>`
      + `<div class="muted">변동성 칸에는 ${useIqr ? '1년·IQR' : 'Temp S_vol'} 평균을 넣었습니다 (위 '변동성 방식' 선택을 따릅니다). Temp DW proxy1~5(FA~FE)·pcode1~5(FM~FQ)로 내보냅니다.</div>`;
    markStale();
  } catch (e) { $('#peerNote').innerHTML = `<span class="warnc">유사기업 변동성을 계산하지 못했습니다: ${esc(e.message)}</span>`; }
  btn.disabled = false;
}

/* ------------------------------------------------------------ 7. 평가·내보내기 */
function payload() {
  const f = st.fields;
  const refix = ['하', '상향', '공모가조정'].includes((f['상하향조정'] || {}).value);
  return {
    meta: {
      company: st.corp.corp_name, corp_code: st.corp.corp_code, stock_code: st.corp.stock_code,
      kind: st.issue.kind, bd_tm: (f['회차'] || {}).value || st.issue.bd_tm, rcept_no: st.issue.rcept_no,
      appraiser: $('#appraiser').value.trim(), val_date: $('#val_date').value, credit_grade: $('#credit_grade').value.trim(),
      steps_per_year: Number($('#steps_per_year').value) || 52, par_value: num($('#par_value').value),
      issue_basis: $('#issue_basis').checked, refix_effect: refix ? 'Y' : 'N', use_tables: $('#use_tables').checked,
      city: st.city || null, key: st.issue.key || null, src: st.issue.src || null,
      measure_date: measureDate(), peers: st.peers || [],
    },
    fields: f,
    market: { S: num($('#S').value), sigma: num($('#sigma').value),
      source: (st.market.source || '수동 입력') + (st.market.volMethod && st.market.source !== '수동 입력' ? ` · 변동성 ${st.market.volMethod}` : '') },
    curves: { rf: curve('#rf'), rd: curve('#rd') },
    report: report(),
    assume: assumePayload(),
    result: st.result ? st.result.result : null,
    alerts: st.alerts,
    conclusion: $('#conclusion').value,
  };
}

async function doValue() {
  if (!st.issue) return;
  const btn = $('#btnValue');
  busy(true, btn);
  try {
    const r = await api('/api/value', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload()) });
    st.result = r;
    renderResult();
    $('#resultCard').classList.remove('hidden');
    $('#resultCard').scrollIntoView({ behavior: 'smooth', block: 'start' });
  } catch (e) { toast(e.message, true, 8000); }
  busy(false, btn);
}

function renderResultSO() {
  const r = st.result.result, i = st.result.info, rep = report();
  const fvc = rep['옵션1개당'] != null ? rep['옵션1개당'] : num((st.fields['회사FV'] || {}).value);
  const qL = r['잔여수량'], qG = r['부여수량'];
  const line = (k, a, b, unit) => {
    const d = (a != null && b != null) ? a - b : null, pct = (d != null && b) ? d / Math.abs(b) : null;
    const f = unit === 'mil' ? fmtMil : (v) => fmt(v, 1);
    return `<tr class="${k.startsWith('옵션') ? 'total' : ''}"><td>${k}</td><td>${a == null ? '' : f(a)}</td><td>${b == null ? '' : f(b)}</td>
      <td class="${pct != null && Math.abs(pct) > 0.01 ? 'big' : ''}">${d == null ? '' : f(d)}</td><td>${pct == null ? '' : (pct * 100).toFixed(2) + '%'}</td></tr>`;
  };
  let h = '<tr><th>구분</th><th>대시보드</th><th>회사·평가사</th><th>차이</th><th>차이율</th></tr>';
  const typ = isOpt() ? ((st.fields['옵션종류'] || {}).value || 'Put') : 'Call';
  h += line(isOpt() ? `${typ} 주당 (이항 · Temp SO 시트, 원)` : '옵션 1개당 (이항 · Temp SO, 원)', r['옵션1개당'], fvc);
  h += line(`블랙숄즈 유럽형 ${typ} (참고, 원)`, r['BS유럽형'], null);
  if (qL != null) h += line(isOpt() ? `${fmt(qL)}주 총액 (백만)` : `잔여 ${fmt(qL)}개 총액 (백만)`, r['전체가치'], fvc != null ? fvc * qL : null, 'mil');
  if (qG != null && qG !== qL) h += line(`부여 ${fmt(qG)}개 총액 (백만)`, r['부여분 총액'], fvc != null ? fvc * qG : null, 'mil');
  $('#resTable').innerHTML = h;
  $('#resTable').classList.remove('stale');
  $('#resInfo').textContent = `측정일 ${i.measure_date} · 격자 ${i.N}단계 · 만기까지 ${i.T.toFixed(3)}년 · Rf(만기) ${(i.rf_T * 100).toFixed(3)}%`
    + ` · 행사 가능 단계 ${i.ex_from}~${i.ex_to} (행사기간 안에서만 조기행사) · u ${i.u}`
    + (isOpt() ? ` · 할인 ${i.discount} · 만기 행사가 ${fmt(i.strike_T)}원` : '');
}

function renderResult() {
  if (optLike()) return renderResultSO();
  const res = st.result.result, rep = report(), face = num((st.fields['잔여총액'] || {}).value) || 0;
  if ($('#issue_basis').checked && face && rep['일반사채'] != null && rep.Put != null) {
    // 발행일 기준(리뷰 양식 1.2): 평가사 값도 전체 = 발행금액, 전환권 = 끼워맞춤
    rep['전체가치'] = face;
    rep['전환권'] = face - rep['일반사채'] - rep.Put - (rep.Call || 0);
  }
  if (rep.Call != null && rep.Put != null && rep['전환권'] != null) rep['파생상품 합계'] = rep.Call + rep.Put + rep['전환권'];
  const rows = ['전체가치', '일반사채', '파생상품 합계', 'Put', '전환권', 'Call'];
  let h = '<tr><th>구분</th><th>대시보드(백만)</th><th>평가사(백만)</th><th>차이(백만)</th><th>차이율</th><th>권면 1만원당</th></tr>';
  rows.forEach((k) => {
    const a = res[k], b = rep[k];
    const d = b == null ? null : a - b;
    const pct = b ? d / Math.abs(b) : null;
    h += `<tr class="${k === '전체가치' ? 'total' : ''}"><td>${k}</td><td class="${a < 0 ? 'neg' : ''}">${fmtMil(a)}</td>
      <td>${b == null ? '' : fmtMil(b)}</td><td class="${pct != null && Math.abs(pct) > 0.05 ? 'big' : ''}">${d == null ? '' : fmtMil(d)}</td>
      <td>${pct == null ? '' : (pct * 100).toFixed(2) + '%'}</td><td>${face ? fmt((a / face) * 10000, 1) : ''}</td></tr>`;
  });
  $('#resTable').innerHTML = h;
  $('#resTable').classList.remove('stale');
  const i = st.result.info;
  $('#resInfo').textContent = `격자 ${i.N}단계 · 잔존 ${i.T.toFixed(3)}년 · Rf(만기) ${(i.rf_T * 100).toFixed(3)}% · Rd(만기) ${(i.rd_T * 100).toFixed(3)}% · Put 행사일 ${i.put_dates}회 · Call 행사일 ${i.call_dates}회 · 만기상환율 ${(i.redem_ratio * 100).toFixed(4)}%`
    + (i.refix ? ` · 리픽싱 반영(첫 조정일 ${i.refix_first}, ${i.refix_s0}단계부터 조정일 ${i.refix_nodes}개)` : ' · 리픽싱 없음')
    + ($('#issue_basis').checked ? ' · 발행일 기준: 전환권 = 발행금액 − 일반사채 − Put − Call' : '');
}

async function doExport(kind) {
  if (!st.issue) return toast('먼저 회사와 회차를 골라 주세요.', true);
  if (kind === 'review' && (!st.result || $('#resTable').classList.contains('stale'))) {
    toast('평가 결과가 없거나 입력이 바뀌어서, 먼저 계산한 뒤 리뷰 엑셀을 만듭니다…', false, 6000);
    await doValue();
    if (!st.result) return;                     // 계산 실패 시 doValue가 오류를 보여 준다
  }
  try {
    const r = await api('/api/export/' + kind, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload()) });
    const blob = await r.blob();
    const cd = r.headers.get('content-disposition') || '';
    const m = cd.match(/filename\*=UTF-8''([^;]+)/);
    const name = m ? decodeURIComponent(m[1]) : kind + '.xlsx';
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob); a.download = name; document.body.appendChild(a); a.click();
    setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
    toast(`${name} 저장`);
  } catch (e) { toast(e.message, true, 8000); }
}

/* ------------------------------------------------------------ 시작 */
function defaultValDate() {
  const t = new Date();
  const qEnd = [[3, 31], [6, 30], [9, 30], [12, 31]];
  let y = t.getFullYear(), best = null;
  for (const yy of [y - 1, y]) for (const [m, d] of qEnd) { const dt = new Date(yy, m - 1, d); if (dt < t) best = dt; }
  return `${best.getFullYear()}-${String(best.getMonth() + 1).padStart(2, '0')}-${String(best.getDate()).padStart(2, '0')}`;
}

window.addEventListener('DOMContentLoaded', () => {
  $('#val_date').value = defaultValDate();
  $('#btnSearch').onclick = doSearch;
  $('#q').addEventListener('keydown', (e) => { if (e.key === 'Enter') doSearch(); });
  $('#btnMarket').onclick = loadMarket;
  $('#btnPeers').onclick = loadPeers;
  $('#btnValue').onclick = doValue;
  $('#btnReview').onclick = () => doExport('review');
  $('#btnDW').onclick = () => doExport('dw');
  $('#btnDW2').onclick = () => doExport('dw');
  $('#btnReview2').onclick = () => doExport('review');
  ['#rf', '#rd', '#credit_grade'].forEach((id) => $(id).addEventListener('input', curveCount));
  ['#S', '#sigma', '#steps_per_year'].forEach((id) => $(id).addEventListener('change', () => { st.market.source = '수동 입력'; markStale(); }));
  $('#val_date').addEventListener('change', () => { if (st.issue) loadIssue(st.issue, true); });
  $('#btnCurves').onclick = async () => { st.gradeManual = false; await loadCredit(); await loadCurves(); };
  $('#placement').addEventListener('change', async () => { st.gradeManual = false; await loadCredit(); await loadCurves(); });
  $('#credit_grade').addEventListener('change', () => { st.gradeManual = true; loadCurves(); renderAssume(); });
  ['#period', '#window'].forEach((id) => $(id).addEventListener('change', loadMarket));
  $('#volMethod').addEventListener('change', () => { applyVolMethod(); markStale(); localStorage.setItem('volMethod', $('#volMethod').value); });
  if (localStorage.getItem('volMethod')) $('#volMethod').value = localStorage.getItem('volMethod');
  document.addEventListener('visibilitychange', () => { if (!document.hidden) checkNew(); });
  $('#issue_basis').addEventListener('change', (e) => {
    const iss = (st.fields['발행일'] || {}).value;
    if (e.target.checked && iss) { $('#val_date').value = iss; if (st.issue) loadIssue(st.issue, true); }
    markStale();
  });
  $('#use_tables').addEventListener('change', markStale);
  fillGradeOptions();
  try {
    const c = JSON.parse(localStorage.getItem('curves') || '{}');
    if (c.rf) $('#rf').value = c.rf;
    if (c.rd) $('#rd').value = c.rd;
    if (c.grade) setGrade(c.grade);
  } catch (e) { /* 무시 */ }
  curveCount();
});
