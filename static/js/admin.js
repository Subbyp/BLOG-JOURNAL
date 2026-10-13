// Admin: markdown preview and image uploads
(function () {
  const previewEl = document.querySelector('[data-preview]');
  const mdInput = document.querySelector('[data-editor] #content');
  if (!previewEl || !mdInput) return;

  function parseMarkdownSimple(text) {
    if (!text) return '';
    const lines = text.replace(/\r\n/g, '\n').split('\n');
    let html = '';
    let inCode = false;
    let codeBuf = [];
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      if (/^\s*```/.test(line)) {
        if (!inCode) {
          inCode = true;
          codeBuf = [];
        } else {
          inCode = false;
          html += '<pre><code>' + escapeHtml(codeBuf.join('\n')) + '</code></pre>\n';
        }
        continue;
      }
      if (inCode) { codeBuf.push(line); continue; }
      if (/^#\s+/.test(line)) {
        html += '<h1>' + escapeHtml(line.replace(/^#\s+/, '')) + '</h1>\n';
      } else if (/^##\s+/.test(line)) {
        const h = escapeHtml(line.replace(/^##\s+/, ''));
        html += '<h2 id="' + slugify(h) + '">' + h + '</h2>\n';
      } else if (/^###\s+/.test(line)) {
        const h = escapeHtml(line.replace(/^###\s+/, ''));
        html += '<h3 id="' + slugify(h) + '">' + h + '</h3>\n';
      } else if (/^>\s+/.test(line)) {
        html += '<blockquote>' + escapeHtml(line.replace(/^>\s+/, '')) + '</blockquote>\n';
      } else if (/^\s*-\s+/.test(line)) {
        // start list if needed (simple)
        if (!/^<ul>/.test(html.slice(-10))) html += '<ul>';
        html += '<li>' + escapeMdInline(escapeHtml(line.replace(/^\s*-\s+/, ''))) + '</li>';
        // close handled crudely not needed for small preview
      } else if (line.trim() === '') {
        // flush list if open
          if (!/<\/ul>$/i.test(html.trim()) && html.trim().endsWith('</li>')) {
          // naive: try to close at paragraph boundary - skip here
        }
        html += '\n';
      } else {
        if (html.trim().endsWith('</li>') && !html.trim().endsWith('</ul>')) {
          // close previous ul before p
          const lastLi = html.lastIndexOf('</li>');
          if (lastLi !== -1) html = html.slice(0, lastLi + 5) + '</ul>\n' + html.slice(lastLi + 5);
        }
        html += '<p>' + escapeMdInline(escapeHtml(line)) + '</p>\n';
      }
    }
    if (inCode) html += '<pre><code>' + escapeHtml(codeBuf.join('\n')) + '</code></pre>\n';
    if (html.trim().endsWith('</li>') && !html.includes('</ul>')) {
      // close any dangling list
      html = html.replace(/(<li>.*<\/li>)\s*$/s, '<ul>$1</ul>\n');
    }
    return html.replace(/\n\n+/g, '\n');
  }

  function escapeHtml(s) {
    return s
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }
  function escapeMdInline(s) {
    return s
      .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
      .replace(/__([^_]+)__/g, '<strong>$1</strong>')
      .replace(/\*([^*]+)\*/g, '<em>$1</em>')
      .replace(/_([^_]+)_/g, '<em>$1</em>')
      .replace(/`([^`]+)`/g, '<code>$1</code>')
      .replace(/\[([^\]]+)\]\(([^\)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
      .replace(/!\[([^\]]*)\]\(([^\)]+)\)/g, '<img src="$2" alt="$1">');
  }
  function slugify(s) {
    return (s || '')
      .toLowerCase()
      .trim()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-+|-+$/g, '');
  }

  function updatePreview() {
    previewEl.innerHTML = parseMarkdownSimple(mdInput.value);
  }
  updatePreview();
  mdInput.addEventListener('input', updatePreview);
  mdInput.addEventListener('change', updatePreview);

  // Insert buttons
  document.querySelectorAll('[data-md-insert]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const tpl = btn.dataset.mdInsert || '';
      const [left, right] = tpl.split('|');
      const start = mdInput.selectionStart;
      const end = mdInput.selectionEnd;
      const val = mdInput.value;
      const sel = val.slice(start, end) || '';
      const insert = (left || '') + sel + (right || '');
      mdInput.setRangeText(insert, start, end, 'end');
      mdInput.focus();
      updatePreview();
    });
  });

  // Image upload
  const imgInput = document.querySelector('[data-image-input]');
  const uploadBtn = document.querySelector('[data-md-upload]');
  const csrf = document.querySelector('meta[name="csrf-token"], input[name="csrf_token"]');
  async function uploadFile(file) {
    const form = new FormData();
    form.append('image', file);
    if (csrf) form.append('csrf_token', csrf.value || csrf.content || document.querySelector('input[name="csrf_token"]').value);
    const resp = await fetch('/admin/media/upload', { method: 'POST', body: form, headers: { 'X-Requested-With': 'XMLHttpRequest' } });
    if (!resp.ok) throw new Error('Upload failed');
    return resp.json();
  }
  if (uploadBtn && imgInput) {
    uploadBtn.addEventListener('click', () => imgInput.click());
    imgInput.addEventListener('change', async () => {
      const file = imgInput.files[0];
      if (!file) return;
      try {
        const res = await uploadFile(file);
        if (res.ok) {
          const alt = res.alt || '';
          const url = res.url || '/media/' + res.filename;
          const ins = '![' + alt + '](' + url + ')';
          const start = mdInput.selectionStart;
          mdInput.setRangeText(ins, start, start, 'end');
          mdInput.focus();
          updatePreview();
        }
      } catch (e) {
        alert('Image upload failed. ' + (e.message || ''));
      } finally {
        imgInput.value = '';
      }
    });
  }

  // Excerpt generator
  const gen = document.querySelector('[data-generate-excerpt]');
  if (gen) {
    gen.addEventListener('click', async () => {
      const url = gen.dataset.url;
      const res = await fetch(url, { headers: { 'X-Requested-With': 'XMLHttpRequest' } });
      if (res.ok) {
        const data = await res.json();
        const ex = document.getElementById('excerpt');
        if (ex && data.excerpt) { ex.value = data.excerpt; }
      }
    });
  }

  // Cover upload
  const coverInput = document.querySelector('[data-cover-input]');
  if (coverInput) {
    coverInput.addEventListener('change', async () => {
      const file = coverInput.files[0];
      if (!file) return;
      try {
        const res = await uploadFile(file);
        if (res.ok) {
          const fn = document.getElementById('cover_image');
          if (fn) fn.value = res.filename;
        }
      } catch (e) { alert('Upload failed'); }
      coverInput.value = '';
    });
    const lbl = document.querySelector('label[for="cover-upload"]');
    if (lbl) lbl.addEventListener('click', () => coverInput.click());
  }
})();
