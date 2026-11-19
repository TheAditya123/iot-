const $ = (id) => document.getElementById(id);
const fmt = (value, suffix = "") => value === undefined || value === null ? "N/A" : `${value}${suffix}`;
const timeFmt = (value) => {
  if (!value) return "—";
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? value : d.toLocaleString();
};

function setText(id, value) { $(id).textContent = value; }

function drawChart(rows) {
  const canvas = $("occupancy-chart");
  const ctx = canvas.getContext("2d");
  const rect = canvas.getBoundingClientRect();
  const ratio = window.devicePixelRatio || 1;
  canvas.width = Math.max(600, Math.round(rect.width * ratio));
  canvas.height = Math.round(280 * ratio);
  ctx.scale(ratio, ratio);
  const w = canvas.width / ratio, h = canvas.height / ratio;
  ctx.clearRect(0, 0, w, h);

  const points = rows.slice().reverse()
    .filter(r => Number.isFinite(Number(r.event?.people_count)))
    .map(r => ({y:Number(r.event.people_count), t:r.event.timestamp}));
  if (!points.length) {
    ctx.fillStyle = "#8ea4bd"; ctx.font = "14px system-ui";
    ctx.fillText("No occupancy samples yet", 20, 40); return;
  }

  const pad = {l:36,r:18,t:18,b:30};
  const maxY = Math.max(1, ...points.map(p => p.y));
  ctx.strokeStyle = "#22344a"; ctx.lineWidth = 1;
  for (let i=0;i<=4;i++) {
    const y = pad.t + (h-pad.t-pad.b) * i/4;
    ctx.beginPath(); ctx.moveTo(pad.l,y); ctx.lineTo(w-pad.r,y); ctx.stroke();
  }
  const xy = points.map((p,i) => ({
    x: points.length === 1 ? (pad.l+w-pad.r)/2 : pad.l + i*(w-pad.l-pad.r)/(points.length-1),
    y: h-pad.b - (p.y/maxY)*(h-pad.t-pad.b)
  }));
  ctx.strokeStyle = "#64d8ff"; ctx.lineWidth = 2.5; ctx.beginPath();
  xy.forEach((p,i) => i ? ctx.lineTo(p.x,p.y) : ctx.moveTo(p.x,p.y)); ctx.stroke();
  ctx.fillStyle = "#61e6a8";
  xy.forEach(p => {ctx.beginPath();ctx.arc(p.x,p.y,3.2,0,Math.PI*2);ctx.fill();});
  ctx.fillStyle = "#8ea4bd"; ctx.font = "11px system-ui";
  ctx.fillText("0", 16, h-pad.b+4); ctx.fillText(String(maxY), 16, pad.t+4);
}

function renderEvents(rows) {
  const body = $("events-body");
  body.innerHTML = "";
  if (!rows.length) {
    const tr = document.createElement("tr"), td = document.createElement("td");
    td.colSpan = 7; td.className = "empty"; td.textContent = "Waiting for real sensor data…";
    tr.appendChild(td); body.appendChild(tr); return;
  }
  rows.forEach(row => {
    const e = row.event || {};
    const values = [
      timeFmt(e.timestamp), e.motion ? "Yes" : "No", fmt(e.people_count),
      fmt(e.temperature_c, " °C"), fmt(e.humidity_pct, " %"),
      fmt(e.inference_ms, " ms"), row.published ? "Synced" : "Pending"
    ];
    const tr = document.createElement("tr");
    values.forEach((value, index) => {
      const td = document.createElement("td"); td.textContent = value;
      if (index === 6) td.className = row.published ? "ok" : "warn";
      tr.appendChild(td);
    });
    body.appendChild(tr);
  });
}

async function refresh() {
  try {
    const [statusR, eventsR, metricsR] = await Promise.all([
      fetch("/api/status", {cache:"no-store"}),
      fetch("/api/events?limit=50", {cache:"no-store"}),
      fetch("/api/metrics", {cache:"no-store"})
    ]);
    if (!statusR.ok || !eventsR.ok || !metricsR.ok) throw new Error("dashboard API failed");
    const status = await statusR.json(), rows = await eventsR.json(), metrics = await metricsR.json();
    const e = status?.event || {};

    setText("motion", status ? (e.motion ? "YES" : "NO") : "—");
    setText("people", fmt(e.people_count));
    setText("temperature", fmt(e.temperature_c, " °C"));
    setText("humidity", fmt(e.humidity_pct, " %"));
    setText("pressure", fmt(e.pressure_hpa, " hPa"));
    setText("summary", e.summary || "No sensor events recorded yet.");
    setText("inference", fmt(e.inference_ms, " ms"));
    setText("avg-inference", fmt(metrics.average_inference_ms, " ms"));
    setText("last-event", timeFmt(e.timestamp));
    setText("event-count", String(metrics.total_events || 0));
    setText("peak-people", fmt(metrics.max_people));
    setText("pending-badge", `${metrics.pending_events || 0} pending`);
    const cloud = (metrics.pending_events || 0) > 0 ? `${metrics.pending_events} pending`
      : (metrics.total_events || 0) > 0 ? "Synced" : "No events";
    setText("cloud-sync", cloud);
    $("cloud-sync").className = (metrics.pending_events || 0) > 0 ? "warn" : "ok";

    setText("device-id", e.device_id || "room-monitor-pi");
    setText("pir-gpio", e.pir_gpio === undefined ? "—" : `BCM ${e.pir_gpio}`);
    setText("inference-backend", e.inference_backend || "—");
    renderEvents(rows);
    drawChart(rows);
    setText("updated", `updated ${new Date().toLocaleTimeString()}`);
    setText("refresh-state", "Live");
  } catch (err) {
    console.error(err);
    setText("refresh-state", "Offline");
  }
}

refresh();
setInterval(refresh, 5000);
window.addEventListener("resize", () => refresh());
