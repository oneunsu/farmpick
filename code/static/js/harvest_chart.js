(() => {
  'use strict';
  const payload = document.getElementById('harvest-chart-data');
  const canvas = document.getElementById('harvest-outlook-chart');
  if (!payload || !canvas) return;
  const error = document.getElementById('harvest-chart-error');
  if (typeof Chart === 'undefined') {
    error.hidden = false;
    error.textContent = '그래프 파일을 불러오지 못했습니다. 새로고침하거나 날짜별 표를 확인해주세요.';
    return;
  }
  const data = JSON.parse(payload.textContent);
  const labels = data.rows.map(row => row.date);
  const issueIndex = labels.indexOf(data.as_of);
  const indexOf = day => day ? labels.indexOf(day) : -1;
  const periodStart = indexOf(data.harvest_start);
  const periodEnd = indexOf(data.harvest_end);
  const target = indexOf(data.target);
  const regions = {
    id: 'harvestRegions',
    beforeDatasetsDraw(chart) {
      const {ctx, chartArea: area, scales: {x}} = chart;
      const step = labels.length > 1 ? x.getPixelForValue(1)-x.getPixelForValue(0) : 1;
      function band(first, last, color) {
        if (last < first || last < 0 || first >= labels.length) return;
        const left = Math.max(area.left, x.getPixelForValue(Math.max(first, 0))-step/2);
        const right = Math.min(area.right, x.getPixelForValue(Math.min(last, labels.length-1))+step/2);
        ctx.fillStyle = color; ctx.fillRect(left, area.top, right-left, area.bottom-area.top);
      }
      ctx.save();
      // 계산 가능한 기간과 성능을 평가한 기간을 구분한다.
      band(issueIndex+1, issueIndex+6, '#f0f1f3');
      band(issueIndex+181, labels.length-1, '#f0f1f3');
      if (data.harvest_start && data.harvest_end) {
        const start = data.harvest_start < labels[0] ? 0 : periodStart;
        const end = data.harvest_end > labels[labels.length-1] ? labels.length-1 : periodEnd;
        if (start >= 0 && end >= start) band(start, end, 'rgba(50, 155, 100, 0.12)');
      }
      ctx.restore();
    },
    afterDatasetsDraw(chart) {
      const {ctx, chartArea: area, scales: {x}} = chart;
      const markers = [[issueIndex, '기준일', '#77877f', 0], [target, '선택한 수확일', '#17644b', 1],
                       [periodStart, '수확 시작', '#5b9276', 2], [periodEnd, '수확 종료', '#5b9276', 3]];
      ctx.save(); ctx.font = '11px SUIT, sans-serif';
      for (const [index, label, color, row] of markers) {
        if (index < 0 || index >= labels.length) continue;
        const pixel = x.getPixelForValue(index);
        ctx.strokeStyle = color; ctx.lineWidth = 1; ctx.setLineDash([3, 4]);
        ctx.beginPath(); ctx.moveTo(pixel, area.top); ctx.lineTo(pixel, area.bottom); ctx.stroke();
        ctx.setLineDash([]); ctx.fillStyle = color;
        const width = ctx.measureText(label).width;
        const left = Math.max(area.left+2, Math.min(pixel+4, area.right-width-2));
        ctx.fillText(label, left, area.top+14+row*14);
      }
      ctx.restore();
    },
  };
  try {
    Chart.defaults.font.family = "'SUIT', sans-serif";
    new Chart(canvas, {
      type: 'line',
      data: {labels, datasets: [
        {label: '공시 실제가', data: data.rows.map(row => row.observed), borderColor: '#347bb6',
         backgroundColor: '#347bb6', pointRadius: 1.5, pointHitRadius: 12, borderWidth: 2, spanGaps: false, tension: 0},
        {label: '예측 참고가', data: data.rows.map(row => row.forecast), borderColor: '#25835d',
         backgroundColor: '#25835d', borderDash: [5, 4], borderWidth: 2, pointRadius: context => context.dataIndex === target ? 5 : 0,
         pointHitRadius: 12, spanGaps: false, tension: 0},
      ]},
      plugins: [regions],
      options: {
        responsive: true, maintainAspectRatio: false,
        interaction: {mode: 'index', intersect: false},
        plugins: {legend: {position: 'bottom'}, tooltip: {callbacks: {
          title: items => items[0]?.label || '',
          label: item => `${item.dataset.label}: ${Number(item.raw).toLocaleString('ko-KR', {maximumFractionDigits: 1})}원/kg`,
          footer: items => items.length && items[0].dataIndex > issueIndex &&
            (items[0].dataIndex-issueIndex < 7 || items[0].dataIndex-issueIndex > 180) ? '이 예측기간은 별도 성능 검증 전입니다.' : '',
        }}},
        scales: {x: {ticks: {maxTicksLimit: canvas.parentElement.clientWidth < 500 ? 3 : 8, maxRotation: 0}, title: {display: true, text: '날짜'}},
                 y: {title: {display: true, text: 'KAMIS 참고가격 (원/kg)'}, ticks: {callback: value => Number(value).toLocaleString('ko-KR')}}},
      },
    });
  } catch {
    error.hidden = false;
    error.textContent = '그래프를 표시하지 못했습니다. 아래 날짜별 표에서 가격을 확인해주세요.';
  }
})();
