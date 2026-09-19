const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.join(__dirname, '..');
const studioSource = fs.readFileSync(
    path.join(root, 'da_selection_ui', 'static', 'hypothesis_studio.js'),
    'utf8'
);
const studioCss = fs.readFileSync(
    path.join(root, 'da_selection_ui', 'static', 'hypothesis_studio.css'),
    'utf8'
);
const sharedCss = fs.readFileSync(
    path.join(root, 'da_selection_ui', 'static', 'style.css'),
    'utf8'
);

function sourceBetween(startMarker, endMarker) {
    const start = studioSource.indexOf(startMarker);
    const end = studioSource.indexOf(endMarker, start);
    assert.notEqual(start, -1, 'missing ' + startMarker);
    assert.notEqual(end, -1, 'missing ' + endMarker);
    return studioSource.slice(start, end);
}

const markdownSource = sourceBetween(
    '    function renderMarkdown(md)',
    '    function setComposerError('
);
const renderMarkdown = new Function(
    'esc',
    'CFG',
    markdownSource + '\nreturn renderMarkdown;'
)((value) => String(value == null ? '' : value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;'), { projectSlug: 'example-project' });

test('project figures render as project-scoped images instead of text links', () => {
    const html = renderMarkdown('![Regulatory Rewiring](reports/figures/regulatory_rewiring.png)');
    assert.match(html, /<img src="\/api\/round_workspace_code_artifact\?project=example-project&amp;path=reports%2Ffigures%2Fregulatory_rewiring.png"/);
    assert.match(html, /alt="Regulatory Rewiring"/);
    assert.match(html, /Figure unavailable:/);
    assert.doesNotMatch(renderMarkdown('![bad](reports/../../private.png)'), /<img/);
    assert.doesNotMatch(renderMarkdown('![remote](https:\/\/example.com\/track.png)'), /<img/);
    assert.doesNotMatch(renderMarkdown('`![example](reports/figures/example.png)`'), /<img/);
    assert.doesNotMatch(renderMarkdown('![hidden](reports/.private/example.png)'), /<img/);
    assert.doesNotMatch(renderMarkdown('![escape](reports/..\\\\private.png)'), /<img/);
    assert.match(renderMarkdown('![Output](./code/outputs/figure.png)'), /<img/);
    assert.match(renderMarkdown('![Analysis](analysis_output/figure.png)'), /<img/);
});

function parseFragment(html) {
    const root = { tagName: '#fragment', attributes: {}, children: [] };
    const stack = [root];
    const voidElements = new Set(['br', 'hr']);
    const tokens = String(html || '').match(/<[^>]+>|[^<]+/g) || [];
    tokens.forEach((token) => {
        if (!token.startsWith('<')) {
            stack[stack.length - 1].children.push({ tagName: '#text', textContent: token });
            return;
        }
        const close = token.match(/^<\/([a-z][a-z0-9]*)>$/i);
        if (close) {
            assert.equal(stack[stack.length - 1].tagName, close[1].toLowerCase());
            stack.pop();
            return;
        }
        const open = token.match(/^<([a-z][a-z0-9]*)([^>]*)>$/i);
        assert.ok(open, 'invalid rendered HTML token: ' + token);
        const attributes = {};
        for (const attribute of open[2].matchAll(/([a-z][a-z0-9-]*)="([^"]*)"/gi)) {
            attributes[attribute[1].toLowerCase()] = attribute[2];
        }
        const node = { tagName: open[1].toLowerCase(), attributes: attributes, children: [] };
        stack[stack.length - 1].children.push(node);
        if (!voidElements.has(node.tagName)) stack.push(node);
    });
    assert.equal(stack.length, 1, 'rendered fragment contains unclosed elements');
    return root;
}

function descendants(node, tagName) {
    const matches = [];
    (node.children || []).forEach((child) => {
        if (child.tagName === tagName) matches.push(child);
        matches.push(...descendants(child, tagName));
    });
    return matches;
}

function directChildren(node, tagName) {
    return (node.children || []).filter((child) => child.tagName === tagName);
}

function orderedListNumbers(list) {
    const start = Number(list.attributes.start || 1);
    return directChildren(list, 'li').map((item, index) =>
        Number(item.attributes.value || (start + index))
    );
}

test('assistant Markdown renders semantic paragraphs, headings, and section labels', () => {
    const html = renderMarkdown(
        'First paragraph.\n\n' +
        'Second paragraph with **inline emphasis**.\n\n' +
        '## Novelty\n\n' +
        '**What\'s genuinely novel here:**\n\n' +
        'Supporting explanation.'
    );

    assert.equal((html.match(/<p(?:\s|>)/g) || []).length, 4);
    assert.match(html, /<h2>Novelty<\/h2>/);
    assert.match(
        html,
        /<p class="hs-markdown-section-label"><strong>What&#39;s genuinely novel here:<\/strong><\/p>/
    );
    assert.match(
        html,
        /<p>Second paragraph with <strong>inline emphasis<\/strong>\.<\/p>/
    );
    assert.doesNotMatch(html, /class="hs-markdown-section-label">Second paragraph/);
});

test('blank-separated repeated 1. markers form one sequential ordered-list DOM', () => {
    const dom = parseFragment(renderMarkdown(
        '1. First item\n\n' +
        '1. Second item\n\n' +
        '1. Third item'
    ));
    const lists = descendants(dom, 'ol');

    assert.equal(lists.length, 1);
    assert.equal(directChildren(lists[0], 'li').length, 3);
    assert.deepEqual(orderedListNumbers(lists[0]), [1, 2, 3]);
});

test('explicit sequential markers remain one correctly numbered list', () => {
    const dom = parseFragment(renderMarkdown(
        '1. First item\n\n' +
        '2. Second item\n\n' +
        '3. Third item'
    ));
    const lists = descendants(dom, 'ol');

    assert.equal(lists.length, 1);
    assert.deepEqual(orderedListNumbers(lists[0]), [1, 2, 3]);
});

test('independent ordered lists restart at their authored starting numbers', () => {
    const dom = parseFragment(renderMarkdown(
        '3. Third item\n\n' +
        '4. Fourth item\n\n' +
        'A separating paragraph.\n\n' +
        '1. Fresh first item\n\n' +
        '1. Fresh second item'
    ));
    const lists = descendants(dom, 'ol');

    assert.equal(lists.length, 2);
    assert.equal(lists[0].attributes.start, '3');
    assert.deepEqual(orderedListNumbers(lists[0]), [3, 4]);
    assert.deepEqual(orderedListNumbers(lists[1]), [1, 2]);
});

test('nested ordered lists preserve independent semantic numbering', () => {
    const dom = parseFragment(renderMarkdown(
        '1. Parent\n' +
        '   1. Nested first\n' +
        '   1. Nested second\n' +
        '1. Sibling'
    ));
    const lists = descendants(dom, 'ol');

    assert.equal(lists.length, 2);
    assert.deepEqual(orderedListNumbers(lists[0]), [1, 2]);
    assert.deepEqual(orderedListNumbers(lists[1]), [1, 2]);
    assert.equal(descendants(directChildren(lists[0], 'li')[0], 'ol')[0], lists[1]);
});

test('multi-paragraph ordered-list items remain inside one list item', () => {
    const dom = parseFragment(renderMarkdown(
        '1. First item paragraph.\n\n' +
        '   Second paragraph in the first item.\n\n' +
        '1. Second item.'
    ));
    const lists = descendants(dom, 'ol');
    const items = directChildren(lists[0], 'li');

    assert.equal(lists.length, 1);
    assert.equal(items.length, 2);
    assert.equal(directChildren(items[0], 'p').length, 2);
    assert.deepEqual(orderedListNumbers(lists[0]), [1, 2]);
});

test('headings and unrelated paragraphs keep ordered lists distinct', () => {
    const dom = parseFragment(renderMarkdown(
        '1. Before heading\n\n' +
        '## Separate section\n\n' +
        '1. After heading\n\n' +
        'Unrelated paragraph.\n\n' +
        '3. Authored restart'
    ));
    const lists = descendants(dom, 'ol');

    assert.equal(lists.length, 3);
    assert.deepEqual(lists.map(orderedListNumbers), [[1], [1], [3]]);
    assert.equal(descendants(dom, 'h2').length, 1);
});

test('chat completion renders the final authoritative message as one document', () => {
    const partialDom = parseFragment(renderMarkdown('1. First item\n\n1. Sec'));
    const finalDom = parseFragment(renderMarkdown(
        '1. First item\n\n1. Second item\n\n1. Third item'
    ));
    const finalLists = descendants(finalDom, 'ol');
    const sendDiscuss = sourceBetween(
        '    async function sendDiscuss(',
        '    function finishLlmEditRequest('
    );

    assert.equal(descendants(partialDom, 'ol').length, 1);
    assert.equal(finalLists.length, 1);
    assert.deepEqual(orderedListNumbers(finalLists[0]), [1, 2, 3]);
    assert.match(sendDiscuss, /content: data\.reply/);
    assert.match(sendDiscuss, /renderThread\(\);/);
});

test('assistant Markdown preserves nested lists, blockquotes, tables, code, and safe links', () => {
    const html = renderMarkdown(
        '- Parent\n' +
        '  - Nested child\n' +
        '- Sibling\n\n' +
        '> Quoted **evidence**.\n\n' +
        '| Gene | Effect |\n' +
        '| --- | --- |\n' +
        '| Atf3 | increased |\n\n' +
        'See [source](https://example.com/paper) and `p < 0.05`.\n\n' +
        '```\nconst unsafe = "<tag>";\n```'
    );

    assert.match(html, /<ul><li>Parent<ul><li>Nested child<\/li><\/ul><\/li><li>Sibling<\/li><\/ul>/);
    assert.match(html, /<blockquote><p>Quoted <strong>evidence<\/strong>\.<\/p><\/blockquote>/);
    assert.match(html, /<table><thead><tr><th>Gene<\/th><th>Effect<\/th><\/tr><\/thead>/);
    assert.match(html, /<td>Atf3<\/td><td>increased<\/td>/);
    assert.match(html, /<a href="https:\/\/example\.com\/paper" rel="noopener noreferrer">source<\/a>/);
    assert.match(html, /<code>p &lt; 0\.05<\/code>/);
    assert.match(html, /<pre><code>const unsafe = &quot;&lt;tag&gt;&quot;;<\/code><\/pre>/);
});

test('raw and unsafe link HTML remains escaped', () => {
    const html = renderMarkdown(
        '<script>alert(1)</script> [click](javascript:alert(1))'
    );
    assert.doesNotMatch(html, /<script>/);
    assert.doesNotMatch(html, /href="javascript:/);
    assert.match(html, /&lt;script&gt;alert\(1\)&lt;\/script&gt;/);
});

test('reading rhythm is scoped to assistant turns and leaves shared/user surfaces alone', () => {
    const scope = '.hs-thread .hs-turn-assistant .hs-turn-body';
    const escapedScope = scope.replace(/[^A-Za-z0-9_-]/g, '\\$&');
    assert.match(studioCss, new RegExp(
        escapedScope + ' \\{[\\s\\S]*?line-height: 1\\.65;'
    ));
    assert.match(studioCss, /margin: 0 0 0\.75rem;/);
    assert.match(studioCss, /\.hs-markdown-section-label \{\s*margin-top: 1\.15rem;/);
    assert.doesNotMatch(studioCss, /\.hs-turn-user \.hs-turn-body p\s*\{/);
    assert.doesNotMatch(sharedCss, /hs-markdown-section-label/);
    assert.match(
        studioSource,
        /m\.role === 'user' \? esc\(m\.content\) : renderMarkdown\(m\.content\)/
    );
});
