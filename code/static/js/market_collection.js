/* 한 요청은 시장 하나·정산일 하루다. 기간 작업은 응답을 확인하며 순차 처리한다. */
(() => {
  'use strict';
  const cropSelect = document.getElementById('market-crop');
  const dateSelect = document.getElementById('market-date');
  if (cropSelect && dateSelect) cropSelect.addEventListener('change', () => { dateSelect.value = ''; });
  const form = document.getElementById('market-collection-form');
  if (!form) return;
  const boxes = Array.from(form.querySelectorAll('[name="market"]'));
  const all = document.getElementById('collection-select-all');
  const selectionCount = document.getElementById('collection-selection-count');
  const workload = document.getElementById('collection-workload');
  const startField = form.elements.namedItem('date');
  const endField = form.elements.namedItem('end_date');
  const message = document.getElementById('collection-message');
  const progress = document.getElementById('collection-progress');
  const log = document.getElementById('collection-log');
  const resultLink = document.getElementById('collection-result');
  const stop = document.getElementById('collection-stop');
  const elapsed = document.getElementById('collection-elapsed');
  let running = false;
  let stopRequested = false;

  function daysInRange() {
    const start = startField.value;
    const end = endField.value || start;
    const first = Date.parse(start + 'T00:00:00Z');
    const last = Date.parse(end + 'T00:00:00Z');
    if (!Number.isFinite(first) || !Number.isFinite(last)) throw new Error('시작일과 종료일을 선택해주세요.');
    if (end < start) throw new Error('종료일은 시작일보다 빠를 수 없습니다.');
    if (start > startField.max || end > endField.max) throw new Error('미래 정산일은 수집할 수 없습니다.');
    const count = Math.round((last - first) / 86400000) + 1;
    if (count > 7) throw new Error('시작일과 종료일을 포함해 최대 7일까지 수집할 수 있습니다.');
    return Array.from({length: count}, (_, index) => new Date(first + index * 86400000).toISOString().slice(0, 10));
  }

  function updateSelection() {
    const count = boxes.filter(box => box.checked).length;
    all.checked = boxes.length > 0 && count === boxes.length;
    all.indeterminate = count > 0 && count < boxes.length;
    all.disabled = !boxes.length;
    selectionCount.textContent = `${boxes.length}개 중 ${count}개 선택`;
    try {
      const days = daysInRange();
      workload.textContent = `${count}개 시장 × ${days.length}일 = ${count * days.length}개 수집 작업. 순차 진행하므로 범위가 클수록 오래 걸립니다.`;
      workload.className = 'small mt-3 mb-0 text-muted';
    } catch (error) {
      workload.textContent = error.message;
      workload.className = 'small mt-3 mb-0 text-danger';
    }
  }
  all.addEventListener('change', () => { boxes.forEach(box => { box.checked = all.checked; }); updateSelection(); });
  boxes.forEach(box => box.addEventListener('change', updateSelection));
  startField.addEventListener('change', updateSelection);
  endField.addEventListener('change', updateSelection);
  updateSelection();
  stop.addEventListener('click', () => {
    stopRequested = true;
    stop.disabled = true;
    stop.textContent = '진행 중인 1건을 마친 뒤 중단합니다';
  });
  window.addEventListener('beforeunload', event => {
    if (running) { event.preventDefault(); event.returnValue = ''; }
  });

  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (running || !form.reportValidity()) return;
    const body = new FormData(form);
    const markets = boxes.filter(box => box.checked).map(box => ({
      value: box.value, name: box.closest('label').textContent.trim(),
    }));
    document.getElementById('collection-status').hidden = false;
    resultLink.hidden = true; log.replaceChildren(); elapsed.textContent = ''; progress.value = 0; progress.max = 1;
    if (!markets.length) { message.textContent = '수집할 시장을 하나 이상 선택해주세요.'; return; }
    let days;
    try { days = daysInRange(); } catch (error) { message.textContent = error.message; return; }
    const jobs = days.flatMap(day => markets.map(market => ({day, market})));
    progress.value = 0; progress.max = jobs.length;
    const controls = Array.from(form.elements);
    controls.forEach(control => { control.disabled = true; });
    running = true; stopRequested = false;
    stop.hidden = false; stop.disabled = false; stop.textContent = '수집 중단';
    let success = 0, failures = 0, zeroRows = 0, processed = 0, lastSuccessDate = null;
    const started = Date.now();
    const updateElapsed = () => {
      const seconds = Math.floor((Date.now() - started) / 1000);
      elapsed.textContent = `경과 ${Math.floor(seconds / 60)}분 ${seconds % 60}초 · 처리 ${processed}/${jobs.length}건`;
    };
    updateElapsed();
    const timer = setInterval(updateElapsed, 1000);
    try {
      for (const job of jobs) {
        if (stopRequested) break;
        message.textContent = `처리 ${processed}/${jobs.length}건 · ${job.day} ${job.market.name} 수집 중`;
        body.set('market', job.market.value);
        body.set('date', job.day); body.set('end_date', job.day);
        const item = document.createElement('li');
        try {
          const response = await fetch(form.action, {method: 'POST', body, credentials: 'same-origin', headers: {'X-CSRFToken': body.get('csrfmiddlewaretoken')}});
          let data;
          try { data = await response.json(); } catch {
            stopRequested = true;
            throw new Error('응답을 확인하지 못해 남은 작업을 중단합니다. 로그인 상태와 연결을 확인해주세요.');
          }
          if (!response.ok || !data.ok) {
            if (data.stop_collection || [401, 403, 409].includes(response.status)) stopRequested = true;
            const details = Object.values(data.errors || {}).flat().map(error => error.message).join(' ');
            throw new Error([data.error || '수집 실패', details].filter(Boolean).join(' '));
          }
          success++; lastSuccessDate = job.day;
          if (data.rows === 0) zeroRows++;
          item.textContent = `${job.day} · ${job.market.name}: ${data.rows.toLocaleString()}건 저장 / 전체 ${data.total.toLocaleString()}건${data.partial ? ' · 일부 표본' : ''}${data.rows === 0 ? ' · 해당 날짜 공시 내역 없음' : ''}`;
        } catch (error) {
          failures++; item.className = 'text-danger';
          item.textContent = `${job.day} · ${job.market.name}: 실패 · ${error.message}`;
          // 연결이 끊어진 경우 동일 실패를 모든 시장·날짜에 반복하지 않는다.
          if (error instanceof TypeError) stopRequested = true;
        }
        log.append(item); processed++; progress.value = processed; updateElapsed();
      }
      const remaining = jobs.length - processed;
      const title = remaining ? '수집 중단' : failures ? '수집 종료' : '수집 완료';
      message.textContent = `${title} · 성공 ${success}건 / 실패 ${failures}건${remaining ? ` / 미실행 ${remaining}건` : ''}${zeroRows ? ` · 공시 내역 없음 ${zeroRows}건` : ''}. ${success ? '완료된 자료는 저장됐습니다.' : '기존 자료는 유지됩니다.'}`;
      if (lastSuccessDate) {
        const url = new URL(window.location.href);
        url.search = new URLSearchParams({farm: body.get('farm'), crop: body.get('crop'), date: lastSuccessDate}).toString();
        resultLink.href = url.toString(); resultLink.hidden = false;
      }
    } finally {
      clearInterval(timer); running = false; stop.hidden = true;
      controls.forEach(control => { control.disabled = false; });
      updateSelection();
    }
  });
})();
