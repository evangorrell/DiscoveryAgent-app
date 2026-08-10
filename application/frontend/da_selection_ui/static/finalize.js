// Redirect to login if the API reports an expired session
(function enforceAuthFetch() {
    if (window.__authFetchWrapped) return;
    const nativeFetch = window.fetch;
    window.fetch = async (...args) => {
        const response = await nativeFetch(...args);
        if (response.status === 401) {
            window.location.href = '/';
        }
        return response;
    };
    window.__authFetchWrapped = true;
})();

// Client-side event logging
function truncateLogValue(value, maxLength = 200) {
    if (typeof value !== 'string') return value;
    if (value.length <= maxLength) return value;
    return value.slice(0, maxLength) + '…';
}

function buildElementPayload(element) {
    if (!element) return {};
    const tag = element.tagName ? element.tagName.toLowerCase() : null;
    const payload = {
        tag,
        id: element.id || null,
        class: typeof element.className === 'string' ? element.className : null,
        name: element.name || null,
        type: element.type || null,
        role: element.getAttribute ? element.getAttribute('role') : null,
        href: element.href || null,
        action: element.getAttribute ? element.getAttribute('data-log-action') : null,
        label: element.getAttribute ? element.getAttribute('data-log-label') : null,
    };
    if (tag && tag !== 'input' && tag !== 'textarea') {
        payload.text = truncateLogValue((element.innerText || '').trim(), 120);
    } else if (element.type === 'password') {
        payload.text = '[redacted]';
    }
    return payload;
}

function sendLogEvent(eventType, payload = {}, useBeacon = false) {
    if (window.__disableClientLogging) return;
    const body = JSON.stringify({ event_type: eventType, payload });
    if (useBeacon && navigator.sendBeacon) {
        const blob = new Blob([body], { type: 'application/json' });
        navigator.sendBeacon('/api/log_event', blob);
        return;
    }
    fetch('/api/log_event', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body,
        keepalive: true,
        credentials: 'include',
    }).catch(() => {});
}

function sendCleanupBeacon() {
    if (!navigator.sendBeacon) return;
    const blob = new Blob([JSON.stringify({})], { type: 'application/json' });
    navigator.sendBeacon('/api/cleanup', blob);
}

function isCleanupArmed() {
    try {
        return localStorage.getItem('da_cleanup_armed') === 'true';
    } catch (err) {
        return false;
    }
}

function setCleanupArmed(enabled) {
    try {
        localStorage.setItem('da_cleanup_armed', enabled ? 'true' : 'false');
    } catch (err) {
        // Ignore storage errors (private mode, disabled storage, etc.)
    }
}

function logEvent(eventType, payload = {}) {
    sendLogEvent(eventType, payload);
}

const HYPOTHESIS_LANDING_HASH = '#hypotheses';
const HYPOTHESIS_LANDING_KEY = 'da_hypotheses_ready';

function markHypothesesLandingReady() {
    try {
        sessionStorage.setItem(HYPOTHESIS_LANDING_KEY, '1');
    } catch (err) {
        // Ignore storage errors.
    }
}

function installBackToLandingGuard() {
    const url = new URL(window.location.href);
    if (!history.state || history.state.view !== 'finalize') {
        history.pushState({ view: 'finalize' }, '', url);
        history.pushState({ view: 'finalize-guard' }, '', url);
    }
    window.addEventListener('popstate', () => {
        try {
            sessionStorage.removeItem('selectedHypothesis');
        } catch (err) {
            // Ignore storage errors.
        }
        window.location.href = `/${HYPOTHESIS_LANDING_HASH}`;
        history.pushState({ view: 'finalize-guard' }, '', url);
    });
}

logEvent('page_view', {
    path: window.location.pathname,
    search: window.location.search,
    referrer: document.referrer || null,
});

document.addEventListener('click', (event) => {
    const target = event.target.closest('[data-log-action], button, a, input, select, textarea');
    if (!target) return;
    logEvent('ui_click', { target: buildElementPayload(target) });
}, { capture: true });

document.addEventListener('submit', (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement)) return;
    logEvent('form_submit', {
        id: form.id || null,
        name: form.name || null,
        action: form.action || null,
        method: form.method || null,
    });
}, { capture: true });

window.addEventListener('error', (event) => {
    logEvent('client_error', {
        message: event.message || 'Unknown error',
        filename: event.filename || null,
        lineno: event.lineno || null,
        colno: event.colno || null,
    });
});

window.addEventListener('unhandledrejection', (event) => {
    logEvent('client_error', {
        message: event.reason ? String(event.reason) : 'Unhandled promise rejection',
    });
});

document.addEventListener('visibilitychange', () => {
    logEvent('visibility_change', { state: document.visibilityState });
});

window.addEventListener('beforeunload', () => {
    sendLogEvent('page_unload', { path: window.location.pathname }, true);
    if (isCleanupArmed()) {
        sendCleanupBeacon();
    }
});

let manualEditInput = null;
let originalHypothesisText = '';
let feedbackApplied = false;
let currentSelectionData = null;
let currentCitations = [];
let currentDocuments = [];
let lastCitations = [];
let citationDocuments = [];
let citationDataMap = {};

// Utilities
function showLoading(message = 'Processing...') {
    const overlay = document.getElementById('loading-overlay');
    const messageEl = document.getElementById('loading-message');
    if (messageEl) messageEl.textContent = message;
    overlay.classList.remove('hidden');
}

function hideLoading() {
    const overlay = document.getElementById('loading-overlay');
    overlay.classList.add('hidden');
}

function showStatus(elementId, message, type) {
    const element = document.getElementById(elementId);
    element.textContent = message;
    element.className = `status-message ${type}`;
    element.style.display = 'block';
}

function hideStatus(elementId) {
    const element = document.getElementById(elementId);
    element.style.display = 'none';
}

function showModal(modalId) {
    document.getElementById(modalId).classList.remove('hidden');
}

function hideModal(modalId) {
    document.getElementById(modalId).classList.add('hidden');
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function applyCodeHighlight(root = document) {
    if (!window.hljs || !root) return;
    const blocks = root.querySelectorAll('pre code');
    blocks.forEach((block) => {
        if (block.dataset.highlighted) return;
        window.hljs.highlightElement(block);
        block.dataset.highlighted = 'true';
    });
}

window.addEventListener('load', () => applyCodeHighlight(document));

function formatMarkdown(text) {
    if (!text) return '';

    const codeBlocks = [];
    const inlineCodes = [];
    const tableBlocks = [];

    const normalizeCodeContent = (codeText) => {
        if (!codeText) return '';
        if (codeText.includes('&lt;') || codeText.includes('&gt;') || codeText.includes('&amp;')) {
            return codeText;
        }
        return escapeHtml(codeText);
    };

    let formatted = text.replace(/```(\w+)?\n([\s\S]*?)```/g, (match, lang, code) => {
        const safe = normalizeCodeContent(code.replace(/\s+$/, ''));
        const langClass = lang ? ` language-${lang}` : '';
        const blockHtml = `<pre><code class="${langClass.trim()}">${safe}</code></pre>`;
        const token = `@@CODEBLOCK${codeBlocks.length}@@`;
        codeBlocks.push(blockHtml);
        return token;
    });

    formatted = formatted.replace(/`([^`]+)`/g, (match, code) => {
        const safe = normalizeCodeContent(code);
        const inlineHtml = `<code>${safe}</code>`;
        const token = `@@INLINECODE${inlineCodes.length}@@`;
        inlineCodes.push(inlineHtml);
        return token;
    });

    formatted = formatted.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
    formatted = formatted.replace(/\*(.*?)\*/g, '<em>$1</em>');
    formatted = formatted.replace(/^### (.*?)$/gm, '<h3>$1</h3>');
    formatted = formatted.replace(/^## (.*?)$/gm, '<h2>$1</h2>');
    formatted = formatted.replace(/^# (.*?)$/gm, '<h1>$1</h1>');

    const splitTableRow = (line) => {
        let trimmed = line.trim();
        if (trimmed.startsWith('|')) trimmed = trimmed.slice(1);
        if (trimmed.endsWith('|')) trimmed = trimmed.slice(0, -1);
        return trimmed.split('|').map(cell => cell.trim());
    };

    const isTableSeparator = (line) => {
        if (!line.includes('|')) return false;
        const cells = splitTableRow(line);
        if (cells.length < 2) return false;
        return cells.every(cell => /^:?-{3,}:?$/.test(cell));
    };

    const buildTableHtml = (headerLine, separatorLine, rowLines) => {
        const headers = splitTableRow(headerLine);
        const aligns = splitTableRow(separatorLine).map(cell => {
            const left = cell.startsWith(':');
            const right = cell.endsWith(':');
            if (left && right) return 'center';
            if (right) return 'right';
            if (left) return 'left';
            return null;
        });

        const headHtml = headers.map((cell, idx) => {
            const align = aligns[idx];
            const alignAttr = align ? ` style="text-align:${align};"` : '';
            return `<th${alignAttr}>${cell}</th>`;
        }).join('');

        const bodyHtml = rowLines.map(rowLine => {
            const cells = splitTableRow(rowLine);
            const rowCells = headers.map((_, idx) => {
                const cell = cells[idx] || '';
                const align = aligns[idx];
                const alignAttr = align ? ` style="text-align:${align};"` : '';
                return `<td${alignAttr}>${cell}</td>`;
            }).join('');
            return `<tr>${rowCells}</tr>`;
        }).join('');

        return `<table><thead><tr>${headHtml}</tr></thead>${bodyHtml ? `<tbody>${bodyHtml}</tbody>` : ''}</table>`;
    };

    const extractTables = (input) => {
        const lines = input.split('\n');
        const output = [];
        let i = 0;
        while (i < lines.length) {
            const line = lines[i];
            if (line && line.includes('|') && i + 1 < lines.length && isTableSeparator(lines[i + 1])) {
                const headerLine = line;
                const separatorLine = lines[i + 1];
                const rowLines = [];
                i += 2;
                while (i < lines.length && lines[i].includes('|') && lines[i].trim() !== '') {
                    rowLines.push(lines[i]);
                    i += 1;
                }
                const tableHtml = buildTableHtml(headerLine, separatorLine, rowLines);
                const token = `@@TABLE${tableBlocks.length}@@`;
                tableBlocks.push(tableHtml);
                output.push(token);
                continue;
            }
            output.push(line);
            i += 1;
        }
        return output.join('\n');
    };

    formatted = extractTables(formatted);

    formatted = formatted.split('\n\n').map(para => {
        para = para.trim();
        if (!para) return '';

        if (/^@@CODEBLOCK\d+@@$/.test(para)) return para;
        if (/^@@TABLE\d+@@$/.test(para)) return para;

        if (para.startsWith('<ul>') || para.startsWith('<ol>')) return para;

        if (para.match(/<h[1-6]>/)) {
            const lines = para.split('\n');
            return lines.map((line, idx) => {
                if (line.match(/<\/h[1-6]>/) || idx === lines.length - 1) {
                    return line;
                }
                if (idx < lines.length - 1 && lines[idx + 1].match(/<h[1-6]>/)) {
                    return line;
                }
                return line + '<br>';
            }).join('\n');
        }

        return `<p>${para.replace(/\n/g, '<br>')}</p>`;
    }).join('\n');

    formatted = formatted.replace(/^- (.*?)$/gm, '<li>$1</li>');
    formatted = formatted.replace(/(<li>.*<\/li>\n?)+/g, '<ul>$&</ul>');

    formatted = formatted.replace(/@@CODEBLOCK(\d+)@@/g, (match, idx) => codeBlocks[Number(idx)] || '');
    formatted = formatted.replace(/@@TABLE(\d+)@@/g, (match, idx) => tableBlocks[Number(idx)] || '');
    formatted = formatted.replace(/@@INLINECODE(\d+)@@/g, (match, idx) => inlineCodes[Number(idx)] || '');

    return formatted;
}

function truncateText(text, maxLength = 280) {
    if (!text) return '';
    if (text.length <= maxLength) return text;
    return `${text.slice(0, maxLength).trim()}...`;
}

// ============================================================================
// Citation Rendering Functions
// ============================================================================

function buildAndCacheCitations(citations) {
    citationDataMap = {};
    let citationIndex = 1;
    if (!citations || citations.length === 0) return;
    citations.forEach(block => {
        if (!block.citations || !Array.isArray(block.citations)) return;
        block.citations.forEach(cit => {
            citationDataMap[citationIndex] = {
                index: citationIndex,
                citation: cit,
                blockText: block.text || '',
                docIndex: cit.document_index !== undefined ? cit.document_index : -1,
                startChar: cit.start_char_index !== undefined ? cit.start_char_index : -1,
                endChar: cit.end_char_index !== undefined ? cit.end_char_index : -1,
                citedText: cit.cited_text || ''
            };
            citationIndex++;
        });
    });
}

function renderCitations(citations, documents = [], startExpanded = false) {
    if (!citations || citations.length === 0) return '';
    buildAndCacheCitations(citations);
    const collapsedClass = startExpanded ? '' : 'collapsed';
    const toggleText = startExpanded ? 'Hide' : 'Show';
    const totalCitations = Object.keys(citationDataMap).length;
    if (totalCitations === 0) return '';

    let citationItems = '';
    for (let i = 1; i <= totalCitations; i++) {
        const citInfo = citationDataMap[i];
        if (!citInfo) continue;
        const sourceTitle = getSourceTitle(citInfo.citation, documents);
        const citedText = citInfo.citedText;
        const location = formatCitationLocation(citInfo.citation);
        const isClickable = citInfo.docIndex >= 0;
        const clickAttr = isClickable ? `data-cit-click="${i}" title="Click to view source document"` : '';
        const clickClass = isClickable ? 'citation-item-clickable' : '';
        citationItems += `<div class="citation-item ${clickClass}" ${clickAttr}><div class="citation-item-header"><span class="citation-source">[${i}] ${escapeHtml(sourceTitle)}</span>${location ? `<span class="citation-location">${escapeHtml(location)}</span>` : ''}</div>${citedText ? `<div class="citation-quote">"${escapeHtml(truncateText(citedText, 300))}"</div>` : ''}</div>`;
    }

    return `<div class="citations-container"><div class="citations-header"><span class="citation-badge"><span class="citation-badge-icon">📚</span>${totalCitations} citation${totalCitations > 1 ? 's' : ''}</span><button type="button" class="citations-toggle" onclick="toggleCitations(this)" data-expanded="${startExpanded}">${toggleText}</button></div><div class="citations-list ${collapsedClass}">${citationItems}</div></div>`;
}

function getSourceTitle(citation, documents) {
    if (documents && documents.length > 0 && citation.document_index !== undefined) {
        const doc = documents[citation.document_index];
        if (doc && doc.title) {
            return doc.title;
        }
    }
    if (citation.type === 'char_location') {
        return 'Source Document';
    }
    return citation.source || 'Reference';
}

function formatCitationLocation(citation) {
    if (citation.type === 'char_location') {
        const start = citation.start_char_index;
        const end = citation.end_char_index;
        if (start !== undefined && end !== undefined) {
            return `chars ${start}-${end}`;
        }
    }
    return '';
}

function toggleCitations(button) {
    const container = button.closest('.citations-container');
    const list = container.querySelector('.citations-list');
    const isExpanded = button.dataset.expanded === 'true';

    if (isExpanded) {
        list.classList.add('collapsed');
        button.textContent = 'Show';
        button.dataset.expanded = 'false';
    } else {
        list.classList.remove('collapsed');
        button.textContent = 'Hide';
        button.dataset.expanded = 'true';
    }
}

async function handleCitationClick(citationIndex) {
    const citInfo = citationDataMap[citationIndex];
    if (!citInfo || citInfo.docIndex < 0) {
        alert('Citation source not available');
        return;
    }
    showLoading('Loading cited document...');
    try {
        const response = await fetch('/api/get_citation_document', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ document_index: citInfo.docIndex })
        });
        const result = await response.json();
        if (!result.success) {
            hideLoading();
            alert('Error: ' + result.error);
            return;
        }
        const title = result.title || 'Document';
        let content = result.content || '';
        document.getElementById('modal-title').textContent = 'Citation [' + citationIndex + '] - ' + title;
        let formattedContent = '';
        const startChar = citInfo.startChar;
        const endChar = citInfo.endChar;
        if (startChar >= 0 && endChar > startChar && endChar <= content.length) {
            const HIGHLIGHT_START = '\u0000CITE_START\u0000';
            const HIGHLIGHT_END = '\u0000CITE_END\u0000';
            const contentWithMarkers = content.slice(0, startChar) + HIGHLIGHT_START + content.slice(startChar, endChar) + HIGHLIGHT_END + content.slice(endChar);
            formattedContent = formatMarkdown(contentWithMarkers);
            formattedContent = formattedContent.replace(HIGHLIGHT_START, '<span id="citation-highlight" class="citation-highlight">').replace(HIGHLIGHT_END, '</span>');
        } else {
            formattedContent = formatMarkdown(content);
        }
        document.getElementById('modal-report-content').innerHTML = formattedContent;
        applyCodeHighlight(document.getElementById('modal-report-content'));
        showModal('report-modal');
        setTimeout(function() {
            const highlightEl = document.getElementById('citation-highlight');
            if (highlightEl) {
                highlightEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
            }
        }, 100);
    } catch (error) {
        alert('Error loading document: ' + error.message);
    } finally {
        hideLoading();
    }
}

function openCitationDocument(citationIndex, docIndex, startChar, endChar) {
    handleCitationClick(citationIndex);
}

function handleCitationHover(citationIndex, highlight) {
    const citInfo = citationDataMap[citationIndex];
    if (!citInfo || !citInfo.blockText) return;
    document.querySelectorAll('.citation-text-highlight').forEach(function(el) {
        el.replaceWith(document.createTextNode(el.textContent));
    });
    if (highlight && citInfo.blockText.length > 10) {
        document.querySelectorAll('.hypothesis-text, .hypothesis-section').forEach(function(container) {
            highlightTextInContainer(container, citInfo.blockText);
        });
    }
}

function highlightTextInContainer(container, searchText) {
    const text = container.textContent;
    const searchSnippet = searchText.slice(0, Math.min(80, searchText.length));
    const index = text.toLowerCase().indexOf(searchSnippet.toLowerCase());
    if (index === -1) return;
    const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT, null, false);
    let currentPos = 0;
    let node;
    while (node = walker.nextNode()) {
        const nodeLen = node.textContent.length;
        if (currentPos + nodeLen > index) {
            const startInNode = index - currentPos;
            const endInNode = Math.min(startInNode + searchSnippet.length, nodeLen);
            if (endInNode - startInNode > 5) {
                const before = node.textContent.slice(0, startInNode);
                const match = node.textContent.slice(startInNode, endInNode);
                const after = node.textContent.slice(endInNode);
                const span = document.createElement('span');
                span.className = 'citation-text-highlight';
                span.textContent = match;
                const parent = node.parentNode;
                const frag = document.createDocumentFragment();
                if (before) frag.appendChild(document.createTextNode(before));
                frag.appendChild(span);
                if (after) frag.appendChild(document.createTextNode(after));
                parent.replaceChild(frag, node);
            }
            break;
        }
        currentPos += nodeLen;
    }
}

function addInlineCitationMarkers(text, citations) {
    if (!citations || citations.length === 0 || !text) {
        return { annotatedText: text, citationMap: [] };
    }
    if (Object.keys(citationDataMap).length === 0) {
        buildAndCacheCitations(citations);
    }
    let annotatedText = text;
    const wrappedRanges = [];

    for (const idx in citationDataMap) {
        const citInfo = citationDataMap[idx];
        if (!citInfo.blockText || citInfo.blockText.length < 10) continue;

        const searchText = citInfo.blockText.slice(0, Math.min(80, citInfo.blockText.length));
        const searchTextClean = searchText.replace(/\*\*/g, '').replace(/\*/g, '').trim();
        if (searchTextClean.length < 10) continue;

        const matchIndex = findTextNotInSpan(annotatedText, searchTextClean, wrappedRanges);
        if (matchIndex !== -1) {
            const matchEnd = findMatchEnd(annotatedText, matchIndex, citInfo.blockText);
            const matchLength = matchEnd - matchIndex;
            if (matchLength >= 10) {
                const before = annotatedText.slice(0, matchIndex);
                const matched = annotatedText.slice(matchIndex, matchEnd);
                const after = annotatedText.slice(matchEnd);
                const wrapped = '<span class="citation-inline" data-cit="' + citInfo.index + '" data-cit-label="[' + citInfo.index + ']">' + matched + '</span>';
                annotatedText = before + wrapped + after;
                wrappedRanges.push({ start: matchIndex, end: matchIndex + wrapped.length });
            }
        }
    }
    return { annotatedText: annotatedText, citationMap: citationDataMap };
}

function findTextNotInSpan(html, searchText, wrappedRanges) {
    const lowerHtml = html.toLowerCase();
    const lowerSearch = searchText.toLowerCase();
    let startPos = 0;
    while (true) {
        const index = lowerHtml.indexOf(lowerSearch, startPos);
        if (index === -1) return -1;
        const isInWrapped = wrappedRanges.some(r => index >= r.start && index < r.end);
        const beforeText = html.slice(0, index);
        const lastOpenTag = beforeText.lastIndexOf('<');
        const lastCloseTag = beforeText.lastIndexOf('>');
        const insideTag = lastOpenTag > lastCloseTag;
        const lastCitationOpen = beforeText.lastIndexOf('<span class="citation-inline"');
        const lastSpanClose = beforeText.lastIndexOf('</span>');
        const insideCitationSpan = lastCitationOpen > lastSpanClose;
        if (!isInWrapped && !insideTag && !insideCitationSpan) {
            return index;
        }
        startPos = index + 1;
    }
}

function findMatchEnd(html, startIndex, blockText) {
    const maxLen = Math.min(blockText.length, 200);
    let bestEnd = startIndex + Math.min(40, html.length - startIndex);
    const searchArea = html.slice(startIndex, startIndex + maxLen + 50);
    const sentenceEnd = searchArea.search(/[.!?](?=\s|<|$)/);
    if (sentenceEnd !== -1 && sentenceEnd > 20) {
        bestEnd = startIndex + sentenceEnd + 1;
    }
    let candidateEnd = bestEnd;
    while (candidateEnd < html.length) {
        const nextChar = html[candidateEnd];
        if (nextChar === '<') break;
        if (nextChar === '>' || nextChar === undefined) break;
        candidateEnd++;
    }
    while (candidateEnd > startIndex && html[candidateEnd - 1] && html[candidateEnd - 1].match(/\w/)) {
        const nextChar = html[candidateEnd];
        if (!nextChar || !nextChar.match(/\w/)) break;
        candidateEnd++;
    }
    return Math.min(candidateEnd, html.length);
}

function findBestInsertionPoint(fullText, blockText, citedText, usedPositions) {
    const blockStart = fullText.indexOf(blockText);
    if (blockStart === -1) {
        return findInsertionByKeyPhrases(fullText, citedText, usedPositions);
    }

    const blockEnd = blockStart + blockText.length;
    const sentenceEndPattern = /[.!?](?=\s|$|<)/g;
    let lastSentenceEnd = -1;
    let match;
    
    const searchText = fullText.slice(blockStart, Math.min(blockEnd + 50, fullText.length));
    while ((match = sentenceEndPattern.exec(searchText)) !== null) {
        const absolutePos = blockStart + match.index + 1;
        if (!usedPositions.has(absolutePos)) {
            lastSentenceEnd = absolutePos;
        }
    }

    if (lastSentenceEnd !== -1) {
        return lastSentenceEnd;
    }

    if (!usedPositions.has(blockEnd)) {
        return blockEnd;
    }

    return -1;
}

function escapeRegexChars(str) {
    return str.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function findInsertionByKeyPhrases(fullText, citedText, usedPositions) {
    if (!citedText || citedText.length < 10) return -1;

    // Strip markdown formatting characters
    const cleanedText = citedText.replace(/\*\*/g, '').replace(/\*/g, '');
    const words = cleanedText.split(/\s+/).filter(w => w.length > 3);
    if (words.length < 2) return -1;

    for (let windowSize = Math.min(4, words.length); windowSize >= 2; windowSize--) {
        for (let i = 0; i <= words.length - windowSize; i++) {
            // Escape each word for regex safety
            const phrase = words.slice(i, i + windowSize).map(escapeRegexChars).join('\\s+');
            try {
                const regex = new RegExp(phrase, 'i');
                const match = fullText.match(regex);
                if (match) {
                    const endPos = match.index + match[0].length;
                    const afterMatch = fullText.slice(endPos);
                    const sentenceEnd = afterMatch.search(/[.!?](?=\s|$|<)/);
                    if (sentenceEnd !== -1) {
                        const insertPos = endPos + sentenceEnd + 1;
                        if (!usedPositions.has(insertPos)) {
                            return insertPos;
                        }
                    }
                }
            } catch (e) {
                // Skip invalid regex patterns
                continue;
            }
        }
    }

    return -1;
}

function annotateTextWithCitations(text, citations) {
    if (!citations || citations.length === 0) {
        return text;
    }
    const { annotatedText } = addInlineCitationMarkers(text, citations);
    return annotatedText;
}

function buildFullTextFromSections(hyp = {}) {
    const parts = [];
    if (hyp.title) {
        parts.push(`**Title**: ${hyp.title}`);
    }
    if (hyp.hypothesis) {
        parts.push(`**Hypothesis**: ${hyp.hypothesis}`);
    }
    if (hyp.justification) {
        parts.push(`**Justification**: ${hyp.justification}`);
    }
    if (hyp.plan) {
        parts.push(`**Plan**: ${hyp.plan}`);
    }
    return parts.join('\n\n');
}

function getEditableHypothesis(selectionData) {
    if (!selectionData) return '';
    if (selectionData.selection === 'custom') {
        return selectionData.customHypothesis || '';
    }
    const hyp = selectionData.hypothesis || {};
    return hyp.full_text || buildFullTextFromSections(hyp);
}

function populateManualEdit(selectionData) {
    if (!manualEditInput) return;
    originalHypothesisText = getEditableHypothesis(selectionData) || '';
    manualEditInput.value = originalHypothesisText;
}

async function apiCall(endpoint, data = null) {
    const options = {
        method: data ? 'POST' : 'GET',
        headers: { 'Content-Type': 'application/json' }
    };
    if (data) options.body = JSON.stringify(data);
    const response = await fetch(endpoint, options);
    return await response.json();
}

function renderSessionInfo(sessionInfo) {
    const sessionDetails = document.getElementById('session-details-finalize');
    const submissionCount = Number.isInteger(sessionInfo.projectSubmissionCount)
        ? sessionInfo.projectSubmissionCount
        : 0;
    const currentRound = Number.isInteger(sessionInfo.projectRound)
        ? sessionInfo.projectRound
        : submissionCount + 1;
    const submissionLabel = submissionCount === 1 ? '1 submission' : `${submissionCount} submissions`;
    const datasetPreview = sessionInfo.datasetDescription
        ? (sessionInfo.datasetDescription.length > 100
            ? sessionInfo.datasetDescription.substring(0, 100) + '...'
            : sessionInfo.datasetDescription)
        : 'N/A';

    const taskPreview = sessionInfo.taskDescription
        ? (sessionInfo.taskDescription.length > 100
            ? sessionInfo.taskDescription.substring(0, 100) + '...'
            : sessionInfo.taskDescription)
        : null;

    let html = `
        <div class="session-detail-item">
            <div class="session-detail-label">Round</div>
            <div class="session-detail-value">#${currentRound}</div>
            <div class="session-detail-hint">${submissionLabel} so far</div>
        </div>
        <div class="session-detail-item">
            <div class="session-detail-label">Number of Hypotheses</div>
            <div class="session-detail-value">${sessionInfo.nHypotheses || 'N/A'}</div>
        </div>
        <div class="session-detail-item clickable" title="Dataset description preview">
            <div class="session-detail-label">Dataset</div>
            <div class="session-detail-value session-detail-preview">${escapeHtml(datasetPreview)}</div>
        </div>
    `;

    if (taskPreview) {
        html += `
            <div class="session-detail-item clickable" title="Task description preview">
                <div class="session-detail-label">Task</div>
                <div class="session-detail-value session-detail-preview">${escapeHtml(taskPreview)}</div>
            </div>
        `;
    }

    if (typeof sessionInfo.generatePlan === 'boolean') {
        html += `
            <div class="session-detail-item">
                <div class="session-detail-label">Plans</div>
                <div class="session-detail-value">${sessionInfo.generatePlan ? 'Enabled' : 'Disabled'}</div>
            </div>
        `;
    }

    sessionDetails.innerHTML = html;

    const roundBtn = document.getElementById('finalize-view-round-context-btn');
    if (sessionInfo.hasRoundReport) {
        roundBtn.classList.remove('hidden');
    } else {
        roundBtn.classList.add('hidden');
    }
}

function renderSelection(selectionData, citations = [], documents = []) {
    const displayEl = document.getElementById('selected-hypothesis-display');
    const hasCitations = citations && citations.length > 0;
    
    let markup = '';
    if (selectionData.selection === 'custom') {
        let text = formatMarkdown(selectionData.customHypothesis || '');
        if (hasCitations) {
            text = annotateTextWithCitations(text, citations);
        }
        markup = `
            <div class="hypothesis-section">
                <h4>Custom Hypothesis</h4>
                <div class="hypothesis-text">${text}</div>
            </div>
        `;
    } else {
        const hyp = selectionData.hypothesis || {};
        const titleSuffix = hyp.title ? `: ${escapeHtml(hyp.title)}` : '';
        let text = formatMarkdown(hyp.full_text || 'N/A');
        if (hasCitations) {
            text = annotateTextWithCitations(text, citations);
        }
        markup = `
            <div class="hypothesis-section">
                <h4>Hypothesis ${selectionData.selection}${titleSuffix}</h4>
                <div class="hypothesis-text">${text}</div>
            </div>
        `;
    }

    // Add citations reference section if present
    if (hasCitations) {
        const citationsHtml = renderCitations(citations, documents, false);
        if (citationsHtml) {
            markup += `
                <div class="citations-section" style="margin-top: 20px; padding-top: 15px; border-top: 1px solid var(--border-color);">
                    <h5 style="margin-bottom: 10px; color: var(--text-secondary);">Sources Referenced</h5>
                    ${citationsHtml}
                </div>
            `;
        }
    }

    displayEl.innerHTML = markup;
    applyCodeHighlight(displayEl);
    populateManualEdit(selectionData);
}

async function loadSessionInfo() {
    const stored = sessionStorage.getItem('sessionInfo');
    if (stored) {
        return JSON.parse(stored);
    }

    const result = await apiCall('/api/session_info');
    if (result.success) {
        const submissionCount = Number.isInteger(result.project_submission_count)
            ? result.project_submission_count
            : 0;
        const currentRound = Number.isInteger(result.project_round)
            ? result.project_round
            : submissionCount + 1;
        const info = {
            nHypotheses: result.n_hypotheses,
            datasetDescription: result.dataset_description,
            taskDescription: result.task_description,
            hasRoundReport: result.has_round_report,
            generatePlan: result.generate_plan,
            projectSubmissionCount: submissionCount,
            projectRound: currentRound
        };
        sessionStorage.setItem('sessionInfo', JSON.stringify(info));
        return info;
    }

    throw new Error(result.error || 'Unable to load session info');
}

async function showReport(type) {
    showLoading(type === 'background' ? 'Loading background report...' : 'Loading round report...');
    try {
        const result = await apiCall('/api/get_report', { type });
        if (result.success) {
            document.getElementById('modal-title').textContent = type === 'round' ? 'Latest Round Report' : 'Background Report';
            const reportContent = document.getElementById('modal-report-content');
            reportContent.innerHTML = formatMarkdown(result.report);
            reportContent.style.whiteSpace = 'normal';
            applyCodeHighlight(reportContent);
            showModal('report-modal');
        } else {
            alert(`Error: ${result.error}`);
        }
    } catch (error) {
        alert(`Error: ${error.message}`);
    } finally {
        hideLoading();
    }
}

async function confirmSelection(selectionData) {
    const feedback = document.getElementById('finalize-feedback').value.trim();
    const editedText = manualEditInput ? manualEditInput.value.trim() : '';
    const baselineText = (originalHypothesisText || '').trim();
    const hasManualEdits = editedText && editedText !== baselineText;
    if (feedback && !feedbackApplied) {
        const warningEl = document.getElementById('finalize-feedback-warning');
        if (warningEl) {
            warningEl.classList.remove('hidden');
        }
        showStatus('finalize-status', 'Apply feedback before saving or clear it.', 'error');
        return;
    }
    showLoading('Saving hypothesis selection to server, do not close browser');
    hideStatus('finalize-status');

    try {
        let payloadSelection = selectionData.selection;
        const numericSelection = Number.isInteger(selectionData.selection)
            ? selectionData.selection
            : parseInt(selectionData.selection, 10);
        const selectionStateId = (
            selectionData
            && selectionData.hypothesis
            && typeof selectionData.hypothesis.state_id === 'string'
                ? selectionData.hypothesis.state_id.trim()
                : ''
        ) || null;
        const payload = {
            selection: payloadSelection,
            feedback: feedback || null
        };
        if (Number.isInteger(numericSelection)) {
            if (!selectionStateId) {
                showStatus('finalize-status', 'Hypothesis state is missing. Please go back and reselect.', 'error');
                hideLoading();
                return;
            }
            payload.selection_state_id = selectionStateId;
        }

        const finalText = (hasManualEdits ? editedText : baselineText) || editedText || baselineText;
        if (finalText) {
            payload.final_text = finalText;
        }

        if (selectionData.selection === 'custom') {
            const baseText = selectionData.customHypothesis || baselineText;
            payload.custom_hypothesis = hasManualEdits ? editedText : baseText;
            payload.custom_source = 'direct_custom';
        } else if (hasManualEdits) {
            payloadSelection = 'custom';
            payload.selection = 'custom';
            payload.custom_hypothesis = editedText;
            payload.custom_source = 'base_edit';
            if (Number.isInteger(numericSelection)) {
                payload.base_hypothesis_index = numericSelection;
                if (selectionStateId) {
                    payload.base_hypothesis_state_id = selectionStateId;
                }
            }
        }

        const result = await apiCall('/api/select', payload);

        if (result.success) {
            if (result.cleanup_ready) {
                setCleanupArmed(true);
            }
            window.location.href = '/activity_log';
        } else {
            showStatus('finalize-status', `Error: ${result.error}`, 'error');
        }
    } catch (error) {
        showStatus('finalize-status', `Error: ${error.message}`, 'error');
    } finally {
        hideLoading();
    }
}

async function initFinalizePage() {
    // Set up event delegation for citation clicks
    document.body.addEventListener('click', function(e) {
        // Handle inline citation text clicks (highlighted text)
        const inlineCit = e.target.closest('.citation-inline');
        if (inlineCit && inlineCit.dataset.cit) {
            e.preventDefault();
            e.stopPropagation();
            handleCitationClick(parseInt(inlineCit.dataset.cit, 10));
            return;
        }
        // Handle legacy citation marker clicks
        const marker = e.target.closest('.citation-marker');
        if (marker && marker.dataset.cit) {
            e.preventDefault();
            e.stopPropagation();
            handleCitationClick(parseInt(marker.dataset.cit, 10));
            return;
        }
        // Handle citation item clicks in Sources Referenced list
        const citItem = e.target.closest('[data-cit-click]');
        if (citItem) {
            e.preventDefault();
            e.stopPropagation();
            handleCitationClick(parseInt(citItem.dataset.citClick, 10));
            return;
        }
    });

    const storedSelection = sessionStorage.getItem('selectedHypothesis');
    if (!storedSelection) {
        window.location.href = '/';
        return;
    }

    markHypothesesLandingReady();
    installBackToLandingGuard();

    const selectionData = JSON.parse(storedSelection);
    currentSelectionData = selectionData;
    manualEditInput = document.getElementById('finalize-edit-input');
    try {
        const sessionInfo = await loadSessionInfo();
        renderSessionInfo(sessionInfo);
        // Pass citations from stored selection data
        currentCitations = selectionData.citations || [];
        currentDocuments = selectionData.documents || [];
        renderSelection(selectionData, currentCitations, currentDocuments);
    } catch (error) {
        showStatus('finalize-status', `Error: ${error.message}`, 'error');
    }

    document.getElementById('finalize-view-context-btn').addEventListener('click', () => showReport('background'));
    document.getElementById('finalize-view-round-context-btn').addEventListener('click', () => showReport('round'));

    document.getElementById('finalize-confirm-btn').addEventListener('click', () => confirmSelection(selectionData));
    document.getElementById('finalize-back-btn').addEventListener('click', () => {
        sessionStorage.removeItem('selectedHypothesis');
        window.location.href = `/${HYPOTHESIS_LANDING_HASH}`;
    });

    const feedbackInput = document.getElementById('finalize-feedback');
    if (feedbackInput) {
        feedbackInput.addEventListener('input', () => {
            feedbackApplied = false;
            const warningEl = document.getElementById('finalize-feedback-warning');
            if (warningEl) {
                if (feedbackInput.value.trim()) {
                    warningEl.classList.remove('hidden');
                } else {
                    warningEl.classList.add('hidden');
                }
            }
            const applyFeedbackBtn = document.getElementById('finalize-apply-feedback-btn');
            if (applyFeedbackBtn) {
                applyFeedbackBtn.textContent = 'Save Feedback';
                applyFeedbackBtn.classList.remove('btn-success');
                applyFeedbackBtn.classList.add('btn-secondary');
            }
        });
    }

    // Modal close handlers
    document.querySelectorAll('.modal-close').forEach(btn => {
        btn.addEventListener('click', (e) => {
            const modal = e.target.closest('.modal');
            modal.classList.add('hidden');
        });
    });
    document.querySelectorAll('.modal').forEach(modal => {
        modal.addEventListener('click', (e) => {
            if (e.target === modal) {
                modal.classList.add('hidden');
            }
        });
    });

    const applyFeedbackBtn = document.getElementById('finalize-apply-feedback-btn');
    if (applyFeedbackBtn) {
        applyFeedbackBtn.addEventListener('click', async () => {
            const feedback = document.getElementById('finalize-feedback').value.trim();
            if (!feedback) {
                showStatus('finalize-status', 'Enter feedback to apply.', 'error');
                return;
            }
            const warningEl = document.getElementById('finalize-feedback-warning');
            if (warningEl) {
                warningEl.classList.add('hidden');
            }

            const baseText = (manualEditInput && manualEditInput.value.trim())
                ? manualEditInput.value.trim()
                : (originalHypothesisText || '').trim();

            if (!baseText) {
                showStatus('finalize-status', 'No hypothesis text available to update.', 'error');
                return;
            }

            showLoading('Applying feedback to hypothesis...');
            hideStatus('finalize-status');

            try {
                const result = await apiCall('/api/preview_feedback', {
                    hypothesis_text: baseText,
                    feedback
                });

                if (result.success && result.hypothesis && result.hypothesis.full_text) {
                    const updatedText = result.hypothesis.full_text;
                    originalHypothesisText = updatedText;
                    feedbackApplied = true;

                    if (manualEditInput) {
                        manualEditInput.value = updatedText;
                    }

                    if (currentSelectionData) {
                        if (currentSelectionData.selection === 'custom') {
                            currentSelectionData.customHypothesis = updatedText;
                        } else if (currentSelectionData.hypothesis) {
                            currentSelectionData.hypothesis.full_text = updatedText;
                            currentSelectionData.hypothesis.title = result.hypothesis.title || currentSelectionData.hypothesis.title;
                            currentSelectionData.hypothesis.hypothesis = result.hypothesis.hypothesis;
                            currentSelectionData.hypothesis.justification = result.hypothesis.justification;
                            currentSelectionData.hypothesis.interpretation = result.hypothesis.interpretation;
                            currentSelectionData.hypothesis.plan = result.hypothesis.plan;
                        }
                    }

                    if (result.citations) {
                        currentCitations = result.citations;
                    }
                    if (result.documents) {
                        currentDocuments = result.documents;
                    }

                    renderSelection(currentSelectionData, currentCitations, currentDocuments);
                    applyFeedbackBtn.textContent = 'Feedback Saved';
                    applyFeedbackBtn.classList.remove('btn-secondary');
                    applyFeedbackBtn.classList.add('btn-success');
                    showStatus('finalize-status', 'Feedback saved. Review the updated hypothesis before saving.', 'success');
                } else {
                    showStatus('finalize-status', `Error: ${result.error || 'Unable to apply feedback'}`, 'error');
                }
            } catch (error) {
                showStatus('finalize-status', `Error: ${error.message}`, 'error');
            } finally {
                hideLoading();
            }
        });
    }
}

document.addEventListener('DOMContentLoaded', initFinalizePage);
