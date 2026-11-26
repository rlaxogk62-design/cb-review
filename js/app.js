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
const REPORT_KEYS = ['전체가치', '일반사채', 'Put', '전환권', 'Call'];
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
const KIND_WORD = { CB: '전환', EB: '교환', BW: '행사' };

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
  box.innerHTML = '<span class="muted">발행결정 공시를 불러오는 중…</span>';
  try {
    const list = await api('/api/issues?corp_code=' + c.corp_code);
    box.innerHTML = list.length ? '' : '<span class="muted">CB·BW·EB 발행결정 공시가 없습니다. (비상장 RCPS는 공시에 상세 조건이 없어 직접 입력이 필요합니다)</span>';
    list.forEach((it) => {
      const d = document.createElement('div');
      d.className = 'issue';
      d.innerHTML = `<div class="k">${esc(it.kind)} · 제${esc(it.bd_tm)}회</div>
        <div class="t">${esc(fmt(num(it.bd_fta)))}원</div>
        <div class="d">결정 ${esc(it.bddd || '')} · ${KIND_WORD[it.kind]}가 ${esc(it.price || '-')} · 만기 ${esc(it.mtd || '-')}</div>`;
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
    const r = await api(`/api/load?corp_code=${st.corp.corp_code}&rcept_no=${it.rcept_no}&val_date=${vd}`);
    const prev = st.fields;
    st.fields = r.fields;
    if (keepManual) for (const [k, v] of Object.entries(prev)) if (v && v.manual) st.fields[k] = v;
    st.alerts = r.alerts.map((a) => ({ ...a, checked: a.level !== 'info' || a.cat === '회차' }));
    st.summary = r.summary; st.history = r.history;
    st.disc = {}; (r.disclosures || []).forEach((d) => { st.disc[d.rcept_no] = d; });
    st.disc[it.rcept_no] = st.disc[it.rcept_no] || { report_nm: `${it.kind} 발행결정 (제${it.bd_tm}회)`, date: it.bddd };
    st.result = null;
    $('#work').classList.remove('hidden');
    $('#resultCard').classList.add('hidden');
    if (!keepManual) st.assume = {};
    renderFields(); renderAlerts(); renderReport(); renderAssume();
    if (!keepManual) $('#placement').value = (st.fields['사모여부'] || {}).value === '공모' ? '0' : '1';
    await showDoc(it.rcept_no);
    $('#docTitle').textContent = `${st.corp.corp_name} ${it.kind} 제${it.bd_tm}회 발행결정 원문`;
    (r.warnings || []).forEach((w) => toast(w, true, 6000));
    if (!(r.warnings || []).length) toast('공시 자동 입력 완료. 주가·등급·할인율을 불러오는 중…', false, 5000);
    st.seen = new Set(Object.keys(st.disc));
    startWatch();
  } catch (e) { toast(e.message, true, 8000); busy(false); return false; }
  busy(false);
  await autoMarketData();
  return true;
}

/* ------------------------------------------------------------ 평가기준일 기준 시장자료 자동 수집 */
async function autoMarketData() {
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
  GROUPS.forEach((g) => {
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
  if (g.children.length) return;
  REPORT_KEYS.forEach((k) => {
    const l = document.createElement('label');
    l.innerHTML = `${k}<input data-rep="${k}" placeholder="${k === 'Call' ? '음수로 입력' : '원'}">`;
    l.querySelector('input').addEventListener('change', (e) => { const v = num(e.target.value); e.target.value = v == null ? '' : fmt(v); markStale(); });
    g.appendChild(l);
  });
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
  return ASSUME.map(([k, label, type, get]) => {
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
    const k = el.dataset.as, type = ASSUME.find((a) => a[0] === k)[2];
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
function curveCount() {
  const rf = curve('#rf').length, rd = curve('#rd').length;
  $('#curveCount').textContent = `Rf ${rf}/16 · Rd ${rd}/14`;
  $('#curveCount').className = 'muted' + (rf === 16 && rd === 14 ? '' : ' warnc');
  localStorage.setItem('curves', JSON.stringify({ rf: $('#rf').value, rd: $('#rd').value, grade: $('#credit_grade').value }));
}

async function loadMarket() {
  if (!st.corp || !st.corp.stock_code) { $('#marketNote').textContent = '상장 종목코드가 없어 주가를 불러올 수 없습니다. 직접 입력하세요.'; return; }
  const vd = $('#val_date').value;
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
      city: st.city || null,
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

function renderResult() {
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
