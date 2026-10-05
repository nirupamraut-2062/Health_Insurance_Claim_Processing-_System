/* ClaimSure front-end helpers */
const CS = (() => {
  const palette = ["#2563eb", "#059669", "#d97706", "#7c3aed", "#dc2626", "#0891b2", "#db2777", "#65a30d",
                   "#ea580c", "#4f46e5", "#0d9488", "#9333ea", "#ca8a04", "#64748b", "#be123c", "#15803d"];

  function inr(v) {
    if (v === null || v === undefined || isNaN(v)) return "-";
    return "₹" + Math.round(v).toLocaleString("en-IN");
  }
  function inrShort(v) {
    const a = Math.abs(v);
    if (a >= 1e7) return "₹" + (v / 1e7).toFixed(2) + " Cr";
    if (a >= 1e5) return "₹" + (v / 1e5).toFixed(1) + " L";
    if (a >= 1e3) return "₹" + (v / 1e3).toFixed(0) + "K";
    return "₹" + Math.round(v);
  }

  if (window.Chart) {
    Chart.defaults.font.family = '"Segoe UI", system-ui, sans-serif';
    Chart.defaults.font.size = 12;
    Chart.defaults.color = "#64748b";
    Chart.defaults.plugins.legend.labels.boxWidth = 10;
    Chart.defaults.plugins.legend.labels.boxHeight = 10;
    Chart.defaults.maintainAspectRatio = false;
  }

  const moneyAxis = { ticks: { callback: v => inrShort(v) }, grid: { color: "#eef1f5" } };
  const plainAxis = { grid: { color: "#eef1f5" } };

  function chart(id, config) {
    const el = document.getElementById(id);
    if (!el || !window.Chart) return null;
    if (el._chart) el._chart.destroy();
    el._chart = new Chart(el, config);
    return el._chart;
  }

  function bar(id, labels, datasets, opts = {}) {
    return chart(id, {
      type: "bar",
      data: { labels, datasets: datasets.map((d, i) => ({ borderRadius: 4, maxBarThickness: 38,
        backgroundColor: d.color || palette[i], ...d })) },
      options: {
        indexAxis: opts.horizontal ? "y" : "x",
        plugins: { legend: { display: datasets.length > 1 },
          tooltip: { callbacks: { label: c => `${c.dataset.label}: ${c.dataset.money ? inr(c.raw) : c.raw.toLocaleString("en-IN")}${c.dataset.suffix || ""}` } } },
        scales: opts.scales || {
          [opts.horizontal ? "x" : "y"]: datasets[0].money ? moneyAxis : plainAxis,
          [opts.horizontal ? "y" : "x"]: { grid: { display: false } },
        },
      },
    });
  }

  function doughnut(id, labels, values, colors) {
    return chart(id, {
      type: "doughnut",
      data: { labels, datasets: [{ data: values, backgroundColor: colors || palette, borderWidth: 2, borderColor: "#fff" }] },
      options: { cutout: "62%", plugins: { legend: { position: "right" } } },
    });
  }

  function line(id, labels, datasets, opts = {}) {
    return chart(id, {
      type: "line",
      data: { labels, datasets: datasets.map((d, i) => ({ tension: .3, pointRadius: 2, borderWidth: 2,
        borderColor: d.color || palette[i], backgroundColor: (d.color || palette[i]) + "22", ...d })) },
      options: {
        interaction: { mode: "index", intersect: false },
        plugins: { tooltip: { callbacks: { label: c => `${c.dataset.label}: ${c.dataset.money ? inr(c.raw) : c.raw}` } } },
        scales: opts.scales || { y: datasets[0].money ? moneyAxis : plainAxis, x: { grid: { display: false } } },
      },
    });
  }

  async function report(key) {
    const res = await fetch(`/api/reports/${key}`);
    if (!res.ok) throw new Error(`Report ${key} failed`);
    return res.json();
  }

  return { inr, inrShort, chart, bar, doughnut, line, report, palette, moneyAxis, plainAxis };
})();

const STATUS_COLOR_HEX = {
  "Intimated": "#475569", "Documents Pending": "#d97706", "Under Review": "#2563eb", "Query Raised": "#ea580c",
  "Escalated": "#7c3aed", "Approved": "#0d9488", "Partially Approved": "#0891b2", "Rejected": "#dc2626",
  "Settled": "#059669", "Withdrawn": "#9ca3af",
};
