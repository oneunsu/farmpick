(() => {
  const node = document.getElementById('market-price-data');
  const canvas = document.getElementById('market-price-chart');
  if (!node || !canvas || typeof Chart === 'undefined') return;
  const data = JSON.parse(node.textContent);
  const colors = ['#198754', '#0d6efd', '#b35d00', '#9b348e', '#007f8a', '#ad3434'];
  data.datasets.forEach((series, i) => Object.assign(series, {
    borderColor: colors[i % colors.length], pointRadius: 3, borderWidth: 2,
    borderDash: i < colors.length ? [] : [5, 3], spanGaps: false
  }));
  new Chart(canvas, { type: 'line', data, options: {
    responsive: true, maintainAspectRatio: false, animation: false,
    scales: { y: { title: { display: true, text: '원/kg' } }, x: { ticks: { maxTicksLimit: 10 } } },
    plugins: { legend: { position: 'bottom' } }
  }});
})();
