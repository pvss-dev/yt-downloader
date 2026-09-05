'use strict';

const $ = (id) => document.getElementById(id);

const form = $('download-form');
const urlInput = $('url');
const submitBtn = $('submit-btn');
const preview = $('preview');
const previewError = $('preview-error');
const jobsEl = $('jobs');
const emptyEl = $('empty');
const clearBtn = $('clear-btn');
const optionsSummary = $('options-summary');
const audioOnly = $('audio_only');
const maxHeight = $('max_height');
const audioFormat = $('audio_format');
const template = $('job-template');
const transcribe = $('transcribe');

// job id -> { el, source }
const jobs = new Map();

const STATUS_LABELS = {
  queued: 'Na fila',
  fetching: 'Buscando info',
  downloading: 'Baixando',
  processing: 'Processando',
  loading_model: 'Carregando modelo',
  transcribing: 'Transcrevendo',
  completed: 'Concluído',
  error: 'Erro',
  cancelling: 'Cancelando',
  cancelled: 'Cancelado',
};

const TERMINAL = new Set(['completed', 'error', 'cancelled']);

/* ---------- theme ---------- */

const themeToggle = $('theme-toggle');

function currentTheme() {
  // No attribute means the dark default; the inline head script has already
  // applied any stored preference by now.
  return document.documentElement.dataset.theme === 'light' ? 'light' : 'dark';
}

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  themeToggle.setAttribute(
    'aria-label',
    theme === 'light' ? 'Mudar para tema escuro' : 'Mudar para tema claro',
  );
  try {
    localStorage.setItem('theme', theme);
  } catch (e) {
    // Private mode or blocked storage: the theme still applies for this visit.
  }
}

themeToggle.addEventListener('click', () => {
  applyTheme(currentTheme() === 'light' ? 'dark' : 'light');
});

applyTheme(currentTheme());

/* ---------- formatting ---------- */

function formatBytes(bytes) {
  if (!bytes) return '0 MB';
  const units = ['B', 'KB', 'MB', 'GB'];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(value < 10 && unit > 1 ? 1 : 0)} ${units[unit]}`;
}

function formatSpeed(bytesPerSecond) {
  if (!bytesPerSecond) return null;
  return `${formatBytes(bytesPerSecond)}/s`;
}

function formatEta(seconds) {
  if (seconds === null || seconds === undefined) return null;
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return `${minutes}m ${String(rest).padStart(2, '0')}s`;
}

/* ---------- options summary line ---------- */

function refreshOptionsSummary() {
  const isAudio = audioOnly.checked;
  audioFormat.disabled = !isAudio;
  maxHeight.disabled = isAudio;

  const parts = [];
  if (isAudio) {
    parts.push(audioFormat.value.toUpperCase(), 'áudio');
  } else {
    parts.push(maxHeight.value ? `${maxHeight.value}p` : 'melhor', 'vídeo');
  }
  if ($('playlist').checked) parts.push('playlist');
  if ($('subtitles').checked) parts.push('legendas');

  const wantsTranscript = transcribe.checked;
  $('field-whisper').hidden = !wantsTranscript;
  $('field-language').hidden = !wantsTranscript;
  if (wantsTranscript) parts.push(`transcrição ${$('whisper_model').value}`);

  optionsSummary.textContent = parts.join(' · ');
}

['change', 'input'].forEach((evt) => {
  $('options').addEventListener(evt, refreshOptionsSummary);
});
refreshOptionsSummary();

/* ---------- preview ---------- */

let previewTimer = null;
let previewToken = 0;

function clearPreview() {
  preview.hidden = true;
  previewError.hidden = true;
}

async function fetchPreview(url) {
  const token = ++previewToken;
  try {
    const res = await fetch('/api/info', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url }),
    });

    // A newer keystroke already started another lookup; drop this response.
    if (token !== previewToken) return;

    if (!res.ok) {
      const detail = await res.json().catch(() => ({}));
      previewError.textContent = detail.detail || 'Não foi possível ler esse link.';
      previewError.hidden = false;
      preview.hidden = true;
      return;
    }

    const info = await res.json();
    if (token !== previewToken) return;

    $('preview-thumb').src = info.thumbnail || '';
    $('preview-thumb').hidden = !info.thumbnail;
    $('preview-title').textContent = info.title;
    $('preview-uploader').textContent = info.uploader;
    $('preview-duration').textContent = info.duration;

    const playlistBadge = $('preview-playlist');
    playlistBadge.hidden = !info.is_playlist;
    if (info.is_playlist) {
      playlistBadge.textContent = `Playlist · ${info.entry_count} vídeos`;
      $('playlist').checked = true;
      refreshOptionsSummary();
    }

    previewError.hidden = true;
    preview.hidden = false;
  } catch (err) {
    if (token !== previewToken) return;
    previewError.textContent = 'Falha ao contatar o servidor.';
    previewError.hidden = false;
  }
}

urlInput.addEventListener('input', () => {
  clearTimeout(previewTimer);
  const url = urlInput.value.trim();

  if (!url) {
    previewToken += 1;
    clearPreview();
    return;
  }

  // Debounce so pasting a URL doesn't fire a lookup per character.
  previewTimer = setTimeout(() => fetchPreview(url), 500);
});

/* ---------- job cards ---------- */

function renderJob(job) {
  let entry = jobs.get(job.id);

  if (!entry) {
    const el = template.content.firstElementChild.cloneNode(true);
    el.dataset.id = job.id;
    el.querySelector('.btn-cancel').addEventListener('click', () => cancelJob(job.id));
    jobsEl.prepend(el);
    entry = { el, source: null };
    jobs.set(job.id, entry);
  }

  const { el } = entry;
  el.dataset.status = job.status;

  el.querySelector('.job-title').textContent = job.title || job.url;
  el.querySelector('.job-status').textContent = STATUS_LABELS[job.status] || job.status;

  const thumb = el.querySelector('.job-thumb');
  if (job.thumbnail) {
    thumb.src = job.thumbnail;
    thumb.hidden = false;
  }

  const sub = [];
  if (job.uploader) sub.push(job.uploader);
  if (job.duration) sub.push(job.duration);
  el.querySelector('.job-sub').textContent = sub.join(' · ');

  el.querySelector('.progress-bar').style.width = `${job.percent}%`;

  const stats = [];
  if (job.status === 'downloading') {
    stats.push(`${job.percent.toFixed(1)}%`);
    if (job.stream && job.stream !== '1/1') stats.push(`faixa ${job.stream}`);
    if (job.total_bytes) {
      stats.push(`${formatBytes(job.downloaded_bytes)} / ${formatBytes(job.total_bytes)}`);
    }
    const speed = formatSpeed(job.speed);
    if (speed) stats.push(speed);
    const eta = formatEta(job.eta);
    if (eta) stats.push(`ETA ${eta}`);
  } else if (job.status === 'transcribing') {
    stats.push(`${job.percent.toFixed(1)}%`);
    if (job.transcript_seconds) stats.push(`${Math.round(job.transcript_seconds)}s de áudio`);
  } else if (job.status === 'completed' && job.filepath) {
    // Language first: the filename repeats the title above and gets truncated,
    // so anything after it would be invisible.
    if (job.detected_language) stats.push(`idioma: ${job.detected_language}`);
    stats.push(job.filepath.split('/').pop());
  }
  const statsEl = el.querySelector('.job-stats');
  statsEl.textContent = stats.join('  ·  ');
  statsEl.title = job.status === 'completed' && job.filepath ? job.filepath : '';

  const errorEl = el.querySelector('.job-error');
  if (job.error && job.status !== 'cancelled') {
    errorEl.textContent = job.error;
    errorEl.hidden = false;
  } else {
    errorEl.hidden = true;
  }

  const save = el.querySelector('.btn-save');
  if (job.status === 'completed' && job.filepath) {
    save.href = `/api/jobs/${job.id}/file`;
    save.hidden = false;
  } else {
    save.hidden = true;
  }

  const transcript = el.querySelector('.btn-transcript');
  if (job.transcript_path) {
    transcript.href = `/api/jobs/${job.id}/transcript`;
    transcript.hidden = false;
  } else {
    transcript.hidden = true;
  }

  const preview = el.querySelector('.job-transcript');
  if (job.transcript_preview) {
    preview.textContent = job.transcript_preview;
    preview.hidden = false;
  } else {
    preview.hidden = true;
  }

  refreshChrome();
}

function refreshChrome() {
  emptyEl.hidden = jobs.size > 0;
  const anyFinished = [...jobs.values()].some((e) => TERMINAL.has(e.el.dataset.status));
  clearBtn.hidden = !anyFinished;
}

/* ---------- SSE ---------- */

function subscribe(jobId) {
  const entry = jobs.get(jobId);
  if (!entry || entry.source) return;

  const source = new EventSource(`/api/jobs/${jobId}/events`);
  entry.source = source;

  source.onmessage = (event) => renderJob(JSON.parse(event.data));

  source.addEventListener('done', (event) => {
    renderJob(JSON.parse(event.data));
    source.close();
    entry.source = null;
  });

  source.onerror = () => {
    // The stream ends normally when the job finishes; only surface a problem
    // if the job is still supposed to be running.
    if (!TERMINAL.has(entry.el.dataset.status)) return;
    source.close();
    entry.source = null;
  };
}

async function cancelJob(jobId) {
  await fetch(`/api/jobs/${jobId}/cancel`, { method: 'POST' }).catch(() => {});
}

/* ---------- submit ---------- */

form.addEventListener('submit', async (event) => {
  event.preventDefault();

  const url = urlInput.value.trim();
  if (!url) return;

  submitBtn.disabled = true;
  submitBtn.classList.add('loading');

  const payload = {
    url,
    output_path: $('output_path').value.trim(),
    max_height: maxHeight.value ? Number(maxHeight.value) : null,
    audio_only: audioOnly.checked,
    audio_format: audioFormat.value,
    playlist: $('playlist').checked,
    subtitles: $('subtitles').checked,
    thumbnail: $('thumbnail').checked,
    overwrite: $('overwrite').checked,
    transcribe: transcribe.checked,
    whisper_model: $('whisper_model').value,
    language: $('language').value,
  };

  try {
    const res = await fetch('/api/download', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });

    if (!res.ok) {
      const detail = await res.json().catch(() => ({}));
      previewError.textContent = detail.detail || 'Não foi possível iniciar o download.';
      previewError.hidden = false;
      return;
    }

    const job = await res.json();
    renderJob(job);
    subscribe(job.id);

    urlInput.value = '';
    clearPreview();
    previewToken += 1;
  } catch (err) {
    previewError.textContent = 'Falha ao contatar o servidor.';
    previewError.hidden = false;
  } finally {
    submitBtn.disabled = false;
    submitBtn.classList.remove('loading');
  }
});

clearBtn.addEventListener('click', async () => {
  await fetch('/api/jobs', { method: 'DELETE' }).catch(() => {});
  for (const [id, entry] of jobs) {
    if (TERMINAL.has(entry.el.dataset.status)) {
      entry.source?.close();
      entry.el.remove();
      jobs.delete(id);
    }
  }
  refreshChrome();
});

/* ---------- boot ---------- */

async function boot() {
  try {
    const health = await (await fetch('/api/health')).json();
    $('version').textContent = `yt-dlp ${health.yt_dlp_version}`;
    $('output_path').placeholder = health.default_output;

    if (!health.transcription_available) {
      // Whisper is an optional extra; say how to get it instead of offering a
      // toggle that would only fail on submit.
      transcribe.checked = false;
      transcribe.disabled = true;
      const hint = $('transcribe-hint');
      hint.innerHTML =
        'Transcrição indisponível — instale com <code>pip install -e ".[transcribe]"</code>';
      hint.hidden = false;
      refreshOptionsSummary();
    }
  } catch (err) {
    $('version').textContent = 'servidor offline';
  }

  // Restore jobs still tracked by the server after a page reload.
  try {
    const { jobs: existing } = await (await fetch('/api/jobs')).json();
    for (const job of existing.slice().reverse()) {
      renderJob(job);
      if (!TERMINAL.has(job.status)) subscribe(job.id);
    }
  } catch (err) {
    /* no jobs to restore */
  }

  refreshChrome();
}

boot();
