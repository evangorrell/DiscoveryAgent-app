(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.RoundWorkspaceArtifactLinks = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  const BLOCKED_PARENT_TAGS = new Set(['A', 'PRE', 'SCRIPT', 'STYLE']);

  function safeRelativePath(value) {
    if (typeof value !== 'string') return null;
    const trimmed = value.trim();
    if (!trimmed || trimmed.includes('\0') || trimmed.startsWith('/') || trimmed.includes('\\')) return null;
    if (/^[A-Za-z]:/.test(trimmed)) return null;
    const parts = trimmed.split('/');
    if (parts.some(part => !part || part === '.' || part === '..')) return null;
    return parts.join('/');
  }

  function artifactIdentity(file) {
    return [file.round, file.source || '', file.sourceKind || '', file.artifactPath || file.path || ''].join('\u0000');
  }

  function addCandidate(candidates, key, file) {
    const safe = safeRelativePath(key);
    if (!safe) return;
    const identity = artifactIdentity(file);
    const current = candidates.get(safe);
    if (!current) candidates.set(safe, { identity, file });
    else if (current.identity !== identity) candidates.set(safe, { ambiguous: true });
  }

  function buildIndex(files) {
    const candidates = new Map();
    (Array.isArray(files) ? files : []).forEach(file => {
      if (!file || typeof file !== 'object') return;
      const path = safeRelativePath(file.path);
      const artifactPath = safeRelativePath(file.artifactPath);
      if (!path && !artifactPath) return;
      const normalized = { ...file, path: path || artifactPath, artifactPath: artifactPath || path };
      addCandidate(candidates, normalized.path, normalized);
      addCandidate(candidates, normalized.artifactPath, normalized);
      addCandidate(candidates, (normalized.path || normalized.artifactPath).split('/').pop(), normalized);
    });
    const exact = new Map();
    candidates.forEach((candidate, key) => {
      if (!candidate.ambiguous) exact.set(key, candidate.file);
    });
    return { exact, keys: [...exact.keys()].sort((a, b) => b.length - a.length || a.localeCompare(b)) };
  }

  function resolveReference(value, index) {
    const safe = safeRelativePath(value);
    return safe && index && index.exact instanceof Map ? (index.exact.get(safe) || null) : null;
  }

  function isPathCharacter(value) {
    return Boolean(value && /[A-Za-z0-9_.\\/-]/.test(value));
  }

  function findReferences(text, index) {
    if (typeof text !== 'string' || !index || !Array.isArray(index.keys)) return [];
    const matches = [];
    let cursor = 0;
    while (cursor < text.length) {
      let best = null;
      index.keys.forEach(key => {
        const position = text.indexOf(key, cursor);
        if (position < 0) return;
        if (isPathCharacter(text[position - 1]) || isPathCharacter(text[position + key.length])) return;
        if (!best || position < best.start || (position === best.start && key.length > best.text.length)) {
          best = { start: position, end: position + key.length, text: key, file: index.exact.get(key) };
        }
      });
      if (!best) break;
      matches.push(best);
      cursor = best.end;
    }
    return matches;
  }

  function makeLink(documentRef, label, file, openArtifact) {
    const link = documentRef.createElement('a');
    link.className = 'rw-chat-artifact-link';
    link.href = '#round-workspace-root';
    link.dataset.artifactPath = file.path || file.artifactPath || '';
    link.setAttribute('aria-label', 'Open artifact ' + label);
    link.title = 'Open ' + label + ' in Round Workspace';
    link.addEventListener('click', event => activateArtifact(event, file, openArtifact));
    return link;
  }

  function activateArtifact(event, file, openArtifact) {
    event.preventDefault();
    event.stopPropagation();
    openArtifact(file);
  }

  function linkifyElement(element, index, openArtifact) {
    if (!element || !index || typeof openArtifact !== 'function') return 0;
    let linked = 0;
    function visit(node) {
      if (node.nodeType === 1) {
        if (BLOCKED_PARENT_TAGS.has(node.tagName)) return;
        if (node.tagName === 'CODE') {
          const file = resolveReference(node.textContent, index);
          if (!file) return;
          const link = makeLink(node.ownerDocument, node.textContent, file, openArtifact);
          node.parentNode.replaceChild(link, node);
          link.appendChild(node);
          linked += 1;
          return;
        }
        Array.from(node.childNodes).forEach(visit);
        return;
      }
      if (node.nodeType !== 3 || !node.nodeValue) return;
      const matches = findReferences(node.nodeValue, index);
      if (!matches.length) return;
      const fragment = node.ownerDocument.createDocumentFragment();
      let offset = 0;
      matches.forEach(match => {
        if (match.start > offset) fragment.appendChild(node.ownerDocument.createTextNode(node.nodeValue.slice(offset, match.start)));
        const link = makeLink(node.ownerDocument, match.text, match.file, openArtifact);
        link.textContent = match.text;
        fragment.appendChild(link);
        offset = match.end;
        linked += 1;
      });
      if (offset < node.nodeValue.length) fragment.appendChild(node.ownerDocument.createTextNode(node.nodeValue.slice(offset)));
      node.parentNode.replaceChild(fragment, node);
    }
    visit(element);
    return linked;
  }

  return { safeRelativePath, buildIndex, resolveReference, findReferences, activateArtifact, linkifyElement };
});
