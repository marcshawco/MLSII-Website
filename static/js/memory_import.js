/* History import for /context/admin.
 * Parses chat exports in the browser (ChatGPT, Claude, Grok, Gemini Takeout, or any
 * JSON/text), then uploads conversations in small batches. Nothing is sent until
 * you press Upload. The server extracts facts in the background and wipes the raw
 * text once a conversation is processed. */
(function () {
  'use strict';
  var root = document.getElementById('import-root');
  if (!root) return;
  var CSRF = root.dataset.csrf;
  var USER_MAX = 6000, ASSISTANT_MAX = 800, CONVO_MAX = 140000;
  var BATCH_BYTES = 2500000, BATCH_ITEMS = 200;

  var fileInput = document.getElementById('import-file');
  var sourceSelect = document.getElementById('import-source');
  var previewEl = document.getElementById('import-preview');
  var uploadBtn = document.getElementById('import-upload');
  var processBtn = document.getElementById('import-process');
  var logEl = document.getElementById('import-log');
  var pasteBtn = document.getElementById('import-paste-btn');
  var pasteText = document.getElementById('import-paste');
  var parsed = null, processing = false;

  function log(msg) { logEl.textContent = msg; }
  function hash(s) { var h = 5381; for (var i = 0; i < s.length; i++) h = ((h << 5) + h + s.charCodeAt(i)) | 0; return (h >>> 0).toString(36); }
  function clip(s, n) { s = String(s || '').replace(/\s+\n/g, '\n').trim(); return s.length > n ? s.slice(0, n) + ' […]' : s; }
  function stripHtml(html) { var d = document.createElement('div'); d.innerHTML = String(html || ''); return (d.textContent || '').trim(); }

  function toDate(v) {
    if (v == null || v === '') return null;
    if (typeof v === 'object') {
      if (v.$date !== undefined) return toDate(typeof v.$date === 'object' ? v.$date.$numberLong : v.$date);
      if (v.$numberLong !== undefined) return toDate(Number(v.$numberLong));
      return null;
    }
    if (typeof v === 'string' && /^\d+(\.\d+)?$/.test(v)) v = Number(v);
    var d = typeof v === 'number' ? new Date(v > 1e11 ? v : v * 1000) : new Date(v);
    return isNaN(d.getTime()) ? null : d.toISOString();
  }

  function transcript(messages) {
    var out = [], size = 0, hasUser = false;
    messages.forEach(function (m) {
      var text = String(m.text || '').trim();
      if (!text) return;
      var user = m.role === 'user';
      if (user) hasUser = true;
      var line = (user ? 'user: ' : 'assistant: ') + clip(text, user ? USER_MAX : ASSISTANT_MAX);
      if (size + line.length > CONVO_MAX) return;
      out.push(line); size += line.length + 1;
    });
    return hasUser ? out.join('\n') : '';
  }

  function roleOf(v) {
    v = String(v || '').toLowerCase();
    if (['user', 'human', 'me', 'marcus'].indexOf(v) >= 0) return 'user';
    if (['system', 'tool', 'function'].indexOf(v) >= 0) return 'skip';
    return 'assistant';
  }

  // ---- ChatGPT: conversations.json with a message tree in `mapping`
  function parseChatGPT(list) {
    return list.map(function (c) {
      var map = c.mapping || {}, node = c.current_node, chain = [];
      if (!node || !map[node]) { // fall back to any leaf
        Object.keys(map).forEach(function (k) { if (!(map[k].children || []).length) node = k; });
      }
      var guard = 0;
      while (node && map[node] && guard++ < 10000) { chain.push(map[node]); node = map[node].parent; }
      chain.reverse();
      var msgs = [];
      chain.forEach(function (n) {
        var m = n.message; if (!m || !m.content) return;
        var role = (m.author || {}).role, ct = m.content.content_type, text = '';
        if (ct === 'user_editable_context') {
          text = [m.content.user_profile, m.content.user_instructions].filter(Boolean).join('\n');
          role = 'user';
        } else if (Array.isArray(m.content.parts)) {
          text = m.content.parts.filter(function (p) { return typeof p === 'string'; }).join('\n');
        } else if (typeof m.content.text === 'string') { text = m.content.text; }
        if (roleOf(role) === 'skip') return;
        msgs.push({ role: roleOf(role), text: text });
      });
      return { id: 'chatgpt:' + (c.conversation_id || c.id || hash(JSON.stringify(c).slice(0, 500))),
               title: c.title || '', at: toDate(c.create_time), transcript: transcript(msgs) };
    });
  }

  // ---- Claude: conversations.json with chat_messages
  function parseClaude(list) {
    return list.map(function (c) {
      var msgs = (c.chat_messages || []).map(function (m) {
        var text = m.text;
        if (!text && Array.isArray(m.content)) text = m.content.map(function (p) { return p && p.type === 'text' ? p.text : ''; }).join('\n');
        return { role: roleOf(m.sender), text: text };
      });
      return { id: 'claude:' + (c.uuid || hash(JSON.stringify(c).slice(0, 500))), title: c.name || '',
               at: toDate(c.created_at), transcript: transcript(msgs) };
    });
  }

  // ---- Grok: {conversations: [{conversation: {...}, responses: [{response: {...}}]}]}
  function parseGrok(list) {
    return list.map(function (entry) {
      var conv = entry.conversation || entry, responses = entry.responses || [];
      var msgs = responses.map(function (r) {
        var x = r.response || r;
        return { role: roleOf(x.sender), text: x.message || x.text || '', at: toDate(x.create_time) };
      });
      var at = toDate(conv.create_time) || (msgs[0] && msgs[0].at) || null;
      return { id: 'grok:' + (conv.id || conv.conversation_id || hash(JSON.stringify(entry).slice(0, 500))),
               title: conv.title || '', at: at, transcript: transcript(msgs) };
    });
  }

  // ---- Gemini (Google Takeout "My Activity" JSON): one entry per prompt, grouped by day
  function parseGeminiActivity(list) {
    var days = {};
    list.forEach(function (e) {
      if (!e || !e.time) return;
      var day = String(e.time).slice(0, 10), title = String(e.title || '');
      var prompt = title.replace(/^(Prompted|Asked|Said)\s+/i, '');
      var reply = (e.safeHtmlItem || []).map(function (h) { return stripHtml(h.html); }).join('\n');
      (days[day] = days[day] || []).push({ t: e.time, prompt: prompt, reply: reply });
    });
    return Object.keys(days).map(function (day) {
      var entries = days[day].sort(function (a, b) { return a.t < b.t ? -1 : 1; });
      var msgs = [];
      entries.forEach(function (x) { msgs.push({ role: 'user', text: x.prompt }); msgs.push({ role: 'assistant', text: x.reply }); });
      return { id: 'gemini-day:' + day, title: 'Gemini chats on ' + day, at: toDate(entries[0].t), transcript: transcript(msgs) };
    });
  }

  // ---- Anything else: find arrays of {role/sender/author, content/text/message}
  function looksLikeMessages(arr) {
    if (!Array.isArray(arr) || !arr.length || typeof arr[0] !== 'object' || !arr[0]) return false;
    var m = arr[0];
    return ('role' in m || 'sender' in m || 'author' in m || 'from' in m) && ('content' in m || 'text' in m || 'message' in m || 'body' in m);
  }
  function msgText(m) {
    var c = m.content !== undefined ? m.content : (m.text !== undefined ? m.text : (m.message !== undefined ? m.message : m.body));
    if (typeof c === 'string') return c;
    if (Array.isArray(c)) return c.map(function (p) { return typeof p === 'string' ? p : (p && (p.text || p.content)) || ''; }).join('\n');
    if (c && typeof c === 'object') return c.text || (Array.isArray(c.parts) ? c.parts.join('\n') : '');
    return '';
  }
  function parseGeneric(data, name) {
    var found = [], seen = 0;
    (function walk(node, parent, depth) {
      if (!node || typeof node !== 'object' || depth > 8 || seen++ > 200000) return;
      if (Array.isArray(node)) {
        if (looksLikeMessages(node)) { found.push({ msgs: node, parent: parent || {} }); return; }
        node.forEach(function (x) { walk(x, null, depth + 1); });
        return;
      }
      Object.keys(node).forEach(function (k) { walk(node[k], node, depth + 1); });
    })(data, null, 0);
    return found.map(function (f, i) {
      var p = f.parent, role = function (m) { var a = m.role || m.sender || m.from || m.author; return roleOf(typeof a === 'object' ? a.role || a.name : a); };
      var msgs = f.msgs.map(function (m) { return { role: role(m), text: msgText(m) }; }).filter(function (m) { return m.role !== 'skip'; });
      var at = toDate(p.create_time || p.created_at || p.createdAt || p.timestamp || p.date || (f.msgs[0] || {}).timestamp || (f.msgs[0] || {}).created_at);
      var t = transcript(msgs);
      return { id: 'file:' + name + ':' + (p.id || p.uuid || i) + ':' + hash(t.slice(0, 300)), title: p.title || p.name || name, at: at, transcript: t };
    });
  }

  function parseText(text, name) {
    text = String(text || '').trim();
    if (!text) return [];
    var parts = [], size = 30000;
    for (var i = 0; i < text.length; i += size) parts.push(text.slice(i, i + size));
    return parts.map(function (part, i) {
      return { id: 'text:' + name + ':' + i + ':' + hash(part.slice(0, 300)), title: name + (parts.length > 1 ? ' (part ' + (i + 1) + ')' : ''),
               at: null, transcript: part.split('\n').map(function (l) { return 'user: ' + l; }).join('\n') };
    });
  }

  function detectAndParse(data, name) {
    var list = Array.isArray(data) ? data : null;
    if (data && Array.isArray(data.conversations)) list = data.conversations;
    var first = list && list[0];
    if (first && first.mapping) return { source: 'chatgpt', convos: parseChatGPT(list) };
    if (first && first.chat_messages) return { source: 'claude', convos: parseClaude(list) };
    if (first && (first.responses || (first.conversation && first.conversation.title !== undefined))) return { source: 'grok', convos: parseGrok(list) };
    if (first && first.time && (first.header || first.title) && (first.safeHtmlItem || /gemini|bard/i.test(first.header || ''))) {
      return { source: 'gemini', convos: parseGeminiActivity(list.filter(function (e) { return /gemini|bard/i.test(e.header || '') || e.safeHtmlItem; })) };
    }
    return { source: '', convos: parseGeneric(data, name) };
  }

  function readFile(file) {
    return new Promise(function (resolve, reject) {
      var r = new FileReader(); r.onload = function () { resolve(r.result); }; r.onerror = reject; r.readAsText(file);
    });
  }

  function parseEntry(name, text) {
    var lower = name.toLowerCase();
    if (/\.json$/.test(lower)) {
      try { return detectAndParse(JSON.parse(text), name); } catch (e) { return { source: '', convos: [] }; }
    }
    if (/\.(txt|md|markdown)$/.test(lower)) return { source: '', convos: parseText(text, name) };
    if (/\.html?$/.test(lower)) return { source: /gemini|bard/i.test(name) ? 'gemini' : '', convos: parseText(stripHtml(text), name) };
    return { source: '', convos: [] };
  }

  async function parseFiles(files) {
    var all = [], sources = {};
    for (var i = 0; i < files.length; i++) {
      var f = files[i];
      log('Reading ' + f.name + '…');
      if (/\.zip$/i.test(f.name)) {
        if (!window.JSZip) throw new Error('Zip support failed to load. Unzip the export and pick conversations.json instead.');
        var zip = await window.JSZip.loadAsync(f);
        var names = Object.keys(zip.files).filter(function (n) { return !zip.files[n].dir && /\.(json|txt|md|html?)$/i.test(n) && !/(^|\/)(__MACOSX|\.)/.test(n); });
        for (var j = 0; j < names.length; j++) {
          var res = parseEntry(names[j], await zip.files[names[j]].async('string'));
          if (res.source) sources[res.source] = (sources[res.source] || 0) + 1;
          all = all.concat(res.convos);
        }
      } else {
        var res2 = parseEntry(f.name, await readFile(f));
        if (res2.source) sources[res2.source] = (sources[res2.source] || 0) + 1;
        all = all.concat(res2.convos);
      }
    }
    var unique = {}, convos = [];
    all.forEach(function (c) { if (c.transcript && !unique[c.id]) { unique[c.id] = 1; convos.push(c); } });
    var source = Object.keys(sources).sort(function (a, b) { return sources[b] - sources[a]; })[0] || '';
    return { convos: convos, source: source };
  }

  async function api(path, body) {
    var res = await fetch(path, {
      method: body === undefined ? 'GET' : 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': CSRF },
      body: body === undefined ? undefined : JSON.stringify(body)
    });
    var data = await res.json().catch(function () { return {}; });
    if (!res.ok) throw new Error(data.error || ('HTTP ' + res.status));
    return data;
  }

  fileInput.addEventListener('change', async function () {
    parsed = null; uploadBtn.disabled = true; previewEl.textContent = '';
    if (!fileInput.files.length) return;
    try {
      var result = await parseFiles(fileInput.files);
      parsed = result;
      sourceSelect.value = (result.source && sourceSelect.querySelector('option[value="' + result.source + '"]')) ? result.source : '';
      var dates = result.convos.map(function (c) { return c.at; }).filter(Boolean).sort();
      var chars = result.convos.reduce(function (n, c) { return n + c.transcript.length; }, 0);
      previewEl.textContent = result.convos.length
        ? 'Found ' + result.convos.length + ' conversation(s)' + (result.source ? ' (looks like ' + result.source + ')' : '') +
          (dates.length ? ', from ' + dates[0].slice(0, 10) + ' to ' + dates[dates.length - 1].slice(0, 10) : '') +
          ', ' + (chars < 1000 ? 'under 1k' : 'about ' + Math.round(chars / 1000) + 'k') + ' characters. ' +
          (sourceSelect.value ? 'Check the source below, then Upload.' : 'Pick where it came from below, then Upload.')
        : 'No conversations found in that file. Try conversations.json from the export, or a .txt/.md file.';
      uploadBtn.disabled = !result.convos.length;
      log('');
    } catch (e) { log('Could not read the file: ' + e.message); }
  });

  async function upload(convos, source, filename) {
    var start = await api('/context/admin/import/start', { source_peer: source, filename: filename });
    var sent = 0, added = 0, dup = 0, batch = [], bytes = 0;
    async function flush() {
      if (!batch.length) return;
      var r = await api('/context/admin/import/items', { batch_id: start.batch_id, items: batch });
      added += r.added; dup += r.already_imported; sent += batch.length; batch = []; bytes = 0;
      log('Uploaded ' + sent + ' / ' + convos.length + '…');
    }
    for (var i = 0; i < convos.length; i++) {
      var size = JSON.stringify(convos[i]).length;
      if (batch.length && (bytes + size > BATCH_BYTES || batch.length >= BATCH_ITEMS)) await flush();
      batch.push(convos[i]); bytes += size;
    }
    await flush();
    return { added: added, dup: dup };
  }

  uploadBtn.addEventListener('click', async function () {
    if (!parsed || !parsed.convos.length) return;
    if (!sourceSelect.value) { log('Pick which assistant this history came from.'); return; }
    uploadBtn.disabled = true;
    try {
      var names = Array.prototype.map.call(fileInput.files, function (f) { return f.name; }).join(', ');
      var r = await upload(parsed.convos, sourceSelect.value, names);
      log('Queued ' + r.added + ' conversation(s)' + (r.dup ? ' (' + r.dup + ' were already imported)' : '') +
          '. They are processed in the background, oldest first. You can close this page.');
      parsed = null; fileInput.value = ''; previewEl.textContent = '';
      refreshStatus();
    } catch (e) { log('Upload failed: ' + e.message); uploadBtn.disabled = false; }
  });

  pasteBtn.addEventListener('click', async function () {
    var text = pasteText.value.trim();
    if (!text) return;
    pasteBtn.disabled = true;
    try {
      var r = await upload(parseText(text, 'note ' + new Date().toISOString().slice(0, 10)).map(function (c) {
        c.transcript = c.transcript.replace(/^user: /gm, 'marcus: '); return c;
      }), 'marcus', 'pasted note');
      log('Queued your note (' + r.added + ' part(s)). It will be processed with the next batch.');
      pasteText.value = ''; refreshStatus();
    } catch (e) { log('Could not save the note: ' + e.message); }
    pasteBtn.disabled = false;
  });

  processBtn.addEventListener('click', async function () {
    if (processing) { processing = false; processBtn.textContent = 'Process now'; return; }
    processing = true; processBtn.textContent = 'Stop';
    var total = 0, facts = 0;
    try {
      while (processing) {
        var r = await api('/context/admin/import/step', {});
        total += r.processed; facts += r.facts;
        log('Processed ' + total + ' conversation(s), ' + facts + ' fact(s) so far. ' + r.remaining + ' left.' +
            (r.stopped && r.stopped !== 'time budget used' ? ' Stopped: ' + r.stopped + '.' : ''));
        refreshStatus();
        if (!r.remaining || (r.stopped && r.stopped !== 'time budget used')) break;
      }
    } catch (e) { log('Processing paused: ' + e.message); }
    processing = false; processBtn.textContent = 'Process now';
  });

  async function refreshStatus() {
    try {
      var s = await api('/context/admin/import/status');
      var el = document.getElementById('import-status');
      if (!s.batches.length) { el.textContent = 'No imports yet.'; return; }
      el.innerHTML = '';
      s.batches.forEach(function (b) {
        var row = document.createElement('div'); row.className = 'memory-meta';
        row.textContent = b.source_peer + ' · ' + (b.filename || 'upload') + ' · ' + b.done + '/' + b.total + ' done' +
          (b.queued ? ', ' + b.queued + ' waiting' : '') + (b.failed ? ', ' + b.failed + ' failed' : '') +
          (b.skipped ? ', ' + b.skipped + ' skipped' : '') + ' · ' + b.facts + ' facts' +
          (b.oldest ? ' · ' + String(b.oldest).slice(0, 10) + ' → ' + String(b.newest).slice(0, 10) : '');
        el.appendChild(row);
      });
      if (s.last_error && s.queued) {
        var err = document.createElement('div'); err.className = 'note';
        err.textContent = 'Last error: ' + String(s.last_error.last_error).slice(0, 200); el.appendChild(err);
      }
    } catch (e) { /* status is best-effort */ }
  }
})();
