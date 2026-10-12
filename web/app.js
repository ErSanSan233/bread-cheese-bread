'use strict';

const $ = (s) => document.querySelector(s);
const canvas = $('#wave');
const ctx = canvas.getContext('2d');
const player = $('#player');

const S = {
  jobId: null, duration: 0, peaks: [],
  a: 0, b: 0, c: 0,
  ref: null, refStart: 0, refEnd: 0,
  playhead: 0, dragging: null, poll: null,
};

/* ---------- 时间格式 ---------- */
function fmt(t) {
  if (!isFinite(t)) return '0:00.000';
  const m = Math.floor(t / 60), s = t - m * 60;
  return `${m}:${s.toFixed(3).padStart(6, '0')}`;
}
function parse(s) {
  s = String(s || '').trim();
  let m = s.match(/^(\d+):(\d+(?:\.\d+)?)$/);
  if (m) return +m[1] * 60 + (+m[2]);
  m = s.match(/^(\d+(?:\.\d+)?)$/);
  if (m) return +m[1];
  return NaN;
}

/* ---------- 环境自检 ---------- */
(async function env() {
  try {
    const r = await fetch('/api/health');
    const h = await r.json();
    const mark = (ok, name) => ok ? `<b>${name} ✓</b>` : `<b class="bad">${name} ✗</b>`;
    const opt = (ok, name) => ok ? `<b>${name} ✓</b>` : `<span class="dim">${name} —</span>`;
    $('#env').innerHTML =
      `本机依赖：${mark(h.ffmpeg, 'ffmpeg')} · ${mark(h.tts_python, 'IndexTTS2')} · ` +
      `${mark(h.tts_ckpt, '模型权重')} · ${opt(h.whisper && h.whisper_model, 'whisper 质检')}`;
    if (h.missing && h.missing.length) {
      $('#env').innerHTML +=
        ` <b class="bad">缺：${h.missing.join('、')}</b>` +
        `<span class="dim">（在 bcb.local.env 里配，见 bcb.env.example）</span>`;
    }
  } catch (e) {
    $('#env').textContent = '无法连接后端';
  }
})();

/* ---------- 导入 ---------- */
const drop = $('#drop'), fileInput = $('#file');
drop.onclick = () => fileInput.click();
fileInput.onchange = () => fileInput.files[0] && upload(fileInput.files[0]);
drop.addEventListener('dragover', (e) => { e.preventDefault(); drop.style.borderColor = '#c2410c'; });
drop.addEventListener('dragleave', () => drop.style.borderColor = '');
drop.addEventListener('drop', (e) => {
  e.preventDefault(); drop.style.borderColor = '';
  const f = e.dataTransfer.files[0];
  if (f) upload(f);
});

function upload(file) {
  const fd = new FormData();
  fd.append('file', file);
  $('#loadbar').classList.remove('hidden');
  $('#loadfill').style.width = '5%';
  $('#loadtxt').textContent = `上传并抽音轨：${file.name}`;
  const xhr = new XMLHttpRequest();
  xhr.open('POST', '/api/import');
  xhr.upload.onprogress = (e) => {
    if (e.lengthComputable) {
      const p = Math.min(60, e.loaded / e.total * 60);
      $('#loadfill').style.width = p + '%';
    }
  };
  xhr.onload = () => {
    $('#loadbar').classList.add('hidden');
    let j;
    try { j = JSON.parse(xhr.responseText); } catch { return fail('后端返回异常'); }
    if (!j.ok) return fail(j.error || '导入失败');
    S.jobId = j.id;
    S.duration = j.duration;
    $('#loadtxt').textContent = '';
    $('#step-marks').classList.remove('hidden');
    $('#step-text').classList.remove('hidden');
    $('#step-result').classList.remove('hidden');
    initMarks();
    loadPeaks();
  };
  xhr.onerror = () => { $('#loadbar').classList.add('hidden'); fail('上传失败'); };
  xhr.send(fd);
}

function fail(msg) {
  $('#err').classList.remove('hidden');
  $('#err').textContent = msg;
  $('#step-result').classList.remove('hidden');
}

async function loadPeaks() {
  const r = await fetch(`/api/job/${S.jobId}/waveform`);
  const w = await r.json();
  S.peaks = w.peaks;
  if (w.duration) S.duration = w.duration;
  player.src = `/media/${S.jobId}/hifi.wav`;
  draw();
}

function initMarks() {
  const d = S.duration;
  S.a = Math.min(d * 0.08, Math.max(0, d - 20));
  S.c = Math.min(S.a + Math.min(15, d * 0.35), d);
  S.b = (S.a + S.c) / 2;
  syncInputs();
}

function syncInputs() {
  $('#in-a').value = fmt(S.a);
  $('#in-b').value = fmt(S.b);
  $('#in-c').value = fmt(S.c);
}

/* ---------- 标记约束 ---------- */
function clampMarks(changed) {
  const eps = 0.05, d = S.duration;
  if (changed === 'b') {
    S.b = Math.min(Math.max(S.b, S.a + eps), S.c - eps);
  } else {
    if (S.c <= S.a + 2 * eps) S.c = Math.min(S.a + 2 * eps, d);
    S.b = Math.min(Math.max(S.b, S.a + eps), S.c - eps);
  }
  S.a = Math.max(0, S.a); S.c = Math.min(d, S.c);
  syncInputs(); draw(); scheduleAnalyze();
}

['a', 'b', 'c'].forEach((k) => {
  const el = $('#in-' + k);
  el.addEventListener('change', () => {
    const v = parse(el.value);
    if (isNaN(v)) return syncInputs();
    S[k] = Math.min(Math.max(v, 0), S.duration);
    clampMarks(k);
  });
});

/* ---------- 波形 ---------- */
let resizeTimer = null;
window.addEventListener('resize', () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(draw, 120);
});

function x2t(x) { return x / canvas.clientWidth * S.duration; }
function t2x(t) { return t / S.duration * canvas.clientWidth; }

function draw() {
  const w = canvas.clientWidth, h = canvas.clientHeight;
  if (!w || !h) return;
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);

  // 采样区间 A–C
  ctx.fillStyle = 'rgba(194,65,12,0.07)';
  ctx.fillRect(t2x(S.a), 0, Math.max(0, t2x(S.c) - t2x(S.a)), h);
  // 精选出的参考窗口
  if (S.refEnd > S.refStart) {
    ctx.fillStyle = 'rgba(21,128,61,0.15)';
    ctx.fillRect(t2x(S.refStart), 0, Math.max(0, t2x(S.refEnd) - t2x(S.refStart)), h);
  }

  // 波形
  const n = S.peaks.length;
  if (n) {
    const bw = w / n, mid = h / 2, half = h * 0.40;
    ctx.fillStyle = '#c8bfb6';
    for (let i = 0; i < n; i++) {
      const v = S.peaks[i];
      const bh = Math.max(1, v * half * 2);
      ctx.fillRect(i * bw, mid - bh / 2, Math.max(0.8, bw - 0.35), bh);
    }
    ctx.fillStyle = '#8a8380';
    ctx.fillRect(0, mid, w, 0.5);
  }

  // 播放头
  if (S.playhead > 0) {
    ctx.fillStyle = 'rgba(30,28,26,0.35)';
    ctx.fillRect(t2x(S.playhead), 0, 1, h);
  }

  // 三个标记
  drawMark(S.a, '#15803d', 'A', w, h);
  drawMark(S.b, '#c2410c', 'B', w, h);
  drawMark(S.c, '#b91c1c', 'C', w, h);
}

function drawMark(t, color, label, w, h) {
  const x = t2x(t);
  ctx.fillStyle = color;
  ctx.fillRect(x - 1, 0, 2, h);
  ctx.beginPath();
  ctx.moveTo(x - 7, 0); ctx.lineTo(x + 7, 0); ctx.lineTo(x, 10); ctx.closePath();
  ctx.fill();
  ctx.fillStyle = color;
  ctx.font = 'bold 11px -apple-system,sans-serif';
  ctx.fillText(label, x + 4, 24);
}

/* ---------- 拖动 ---------- */
function nearest(x) {
  const order = [['a', S.a], ['b', S.b], ['c', S.c]];
  let best = null, bd = 9;
  for (const [k, t] of order) {
    const d = Math.abs(t2x(t) - x);
    if (d < bd) { bd = d; best = k; }
  }
  return best;
}
canvas.addEventListener('pointerdown', (e) => {
  const r = canvas.getBoundingClientRect();
  const x = e.clientX - r.left;
  const k = nearest(x);
  if (k) {
    S.dragging = k;
    canvas.setPointerCapture(e.pointerId);
  } else {
    S.playhead = x2t(x);
    player.currentTime = S.playhead;
    draw();
  }
});
canvas.addEventListener('pointermove', (e) => {
  if (!S.dragging) return;
  const r = canvas.getBoundingClientRect();
  S[S.dragging] = Math.min(Math.max(x2t(e.clientX - r.left), 0), S.duration);
  clampMarks(S.dragging);
});
canvas.addEventListener('pointerup', () => { S.dragging = null; });

/* ---------- 播放 ---------- */
player.addEventListener('timeupdate', () => {
  S.playhead = player.currentTime;
  draw();
});
player.addEventListener('ended', () => { S.playhead = 0; draw(); });

function play(from, to) {
  player.pause();
  player.currentTime = from;
  player.ontimeupdate = () => {
    if (to != null && player.currentTime >= to) { player.pause(); player.ontimeupdate = null; }
  };
  player.play();
}
$('#btn-play-ac').onclick = () => play(S.a, S.c);
$('#btn-play-all').onclick = () => play(0, null);
$('#btn-head').onclick = () => { S.b = player.currentTime; clampMarks('b'); };

/* ---------- 参考窗口分析 ---------- */
let anTimer = null;
function scheduleAnalyze() {
  clearTimeout(anTimer);
  anTimer = setTimeout(analyze, 350);
}
async function analyze() {
  if (!S.jobId) return;
  const r = await fetch(`/api/job/${S.jobId}/analyze`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ a: S.a, b: S.b, c: S.c }),
  });
  const j = await r.json();
  if (!j.ok) return;
  S.refStart = j.start; S.refEnd = j.end;
  $('#ref-range').textContent =
    `${fmt(j.start)} → ${fmt(j.end)}　（${(j.end - j.start).toFixed(2)}s` +
    `${j.auto ? '，自动精选' : '，整段'}）`;
  $('#ref-reason').textContent = j.reason || '';
  const st = [];
  if (j.speech_ratio != null) st.push(`语音占比 ${(j.speech_ratio * 100).toFixed(0)}%`);
  if (j.clip_ratio != null) st.push(`削波 ${(j.clip_ratio * 100).toFixed(2)}%`);
  if (j.whole) st.push(`整段 ${j.whole.length.toFixed(1)}s`);
  $('#ref-stats').textContent = st.join(' · ');
  const w = $('#ref-warn');
  if (j.warnings && j.warnings.length) {
    w.classList.remove('hidden');
    w.innerHTML = j.warnings.map((x) => '⚠ ' + x).join('<br>');
  } else w.classList.add('hidden');
  $('#asr-text').textContent = '';
  draw();
}

$('#btn-asr').onclick = async () => {
  const btn = $('#btn-asr');
  btn.disabled = true; btn.textContent = '识别中…';
  try {
    const r = await fetch(`/api/job/${S.jobId}/transcribe-ref`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ start: S.refStart, end: S.refEnd }),
    });
    const j = await r.json();
    $('#asr-text').textContent = j.ok ? `「${j.text}」` : ('识别失败：' + j.error);
  } finally {
    btn.disabled = false; btn.textContent = '🗣 识别参考片段在说什么';
  }
};

/* ---------- 参数 ---------- */
$('#speed').oninput = (e) => $('#speed-v').textContent = (+e.target.value).toFixed(2);

/* ---------- 生成 ---------- */
$('#btn-gen').onclick = async () => {
  const text = $('#text').value.trim();
  if (!text) return fail('先写点旁白');
  $('#err').classList.add('hidden');
  $('#result').innerHTML = '';
  $('#progress').classList.remove('hidden');
  $('#btn-gen').disabled = true;
  const body = {
    a: S.a, b: S.b, c: S.c, text,
    mode: document.querySelector('input[name=mode]:checked').value,
    speed: +$('#speed').value,
    auto_ref: $('#auto-ref').checked,
    pace: $('#pace').checked,
    unify_loudness: $('#unify').checked,
    qc: $('#qc').checked,
  };
  const r = await fetch(`/api/job/${S.jobId}/generate`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const j = await r.json();
  if (!j.ok) {
    $('#btn-gen').disabled = false;
    return fail(j.error || '启动失败');
  }
  S.poll = setInterval(poll, 800);
  poll();
};

async function poll() {
  const r = await fetch(`/api/job/${S.jobId}`);
  const j = await r.json();
  $('#pfill').style.width = (j.progress * 100).toFixed(1) + '%';
  $('#ptxt').textContent = j.message || j.stage;
  if (j.stage === 'done') {
    clearInterval(S.poll);
    $('#btn-gen').disabled = false;
    $('#progress').classList.add('hidden');
    render(j.result);
  } else if (j.stage === 'error') {
    clearInterval(S.poll);
    $('#btn-gen').disabled = false;
    $('#progress').classList.add('hidden');
    fail(j.error || '生成失败');
  }
}

/* ---------- 结果 ---------- */
function render(res) {
  if (!res) return;
  const card = (title, inner) =>
    `<div class="res-card"><h3>${title}</h3>${inner}</div>`;
  const dl = (url, name) =>
    `<div><a href="${url}" download="${name}">下载 ${name}</a></div>`;
  let html = '';

  if (res.wav) {
    html += card('🍞 成品（wav，可直接进剪辑）',
      `<audio controls src="${res.wav}"></audio>` + dl(res.wav, '两面包夹芝士.wav'));
  }
  if (res.mp3) {
    html += card('🎧 成品（mp3，试听用）',
      `<audio controls src="${res.mp3}"></audio>` + dl(res.mp3, '两面包夹芝士.mp3'));
  }
  if (res.narration) {
    html += card('🧈 单独旁白（响度已匹配面包）',
      `<audio controls src="${res.narration}"></audio>` + dl(res.narration, '旁白.wav'));
  }
  if (res.narration_48k) {
    html += card('🧈 单独旁白（48k 单声道 / −16 LUFS，交付标准版）',
      `<audio controls src="${res.narration_48k}"></audio>` +
      dl(res.narration_48k, '旁白_48k.wav'));
  }
  if (res.ref_audio) {
    html += card('🎙 本次使用的音色参考片段',
      `<audio controls src="${res.ref_audio}"></audio>`);
  }

  const t = res.timing || {}, l = res.loudness || {};
  html += card('📐 处理信息', `<div class="kv">
    <span>面包前段 <b>${fmt(t.bread1[0])} → ${fmt(t.bread1[1])}</b></span>
    <span>面包后段 <b>${fmt(t.bread2[0])} → ${fmt(t.bread2[1])}</b></span>
    <span>合成耗时 <b>${t.synth_sec}s</b></span>
    <span>成品时长 <b>${res.out_duration}s</b></span>
    <span>面包响度 <b>${l.bread_i} LUFS</b></span>
    <span>旁白原始 <b>${l.narration_raw_i} LUFS</b></span>
    <span>旁白对齐到 <b>${l.narration_matched_to} LUFS</b>${l.unified ? '（整体统一）' : ''}</span>
  </div>`);

  const q = res.qc;
  if (q && !q.error) {
    const pct = (q.similarity * 100).toFixed(1);
    let d = '';
    if (q.diff_count) {
      d = '<table class="diff"><tr><th>稿子</th><th>回读</th></tr>' +
        q.diffs.map((x) =>
          `<tr><td class="ex">${x.expect || '—'}</td><td class="go">${x.got || '—'}</td></tr>`
        ).join('') + '</table>';
    }
    html += card(`🔍 回读质检 · 相似度 ${pct}%`,
      `<div class="kv"><span>稿子 <b>${q.expect_len}</b> 字</span>
       <span>回读 <b>${q.got_len}</b> 字</span>
       <span>差异处 <b>${q.diff_count}</b></span></div>
       <div class="dim" style="margin-top:6px">回读：${q.asr_text || '（空）'}</div>${d}`);
  } else if (q && q.error) {
    html += card('🔍 回读质检', `<span class="dim">质检没跑起来：${q.error}</span>`);
  }

  $('#result').innerHTML = html;
}
