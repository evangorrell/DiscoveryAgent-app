// Pure, DOM-free event-model helpers for the Hypothesis Studio transcript.
// Extracted so the active-version semantics can be unit-tested in Node while the
// browser controller (hypothesis_studio.js) consumes them via window.HSEvents.
//
// Transcript event shapes (see hypothesis_studio.js):
//   { kind:'message',    role, content }
//   { kind:'hypotheses', id, hypotheses:[{ index, state_id, ... }] }
//   { kind:'revision',   id, index, source:'revised'|'edited', hypothesis:{...} }
//   { kind:'selection',  id, hypothesis:{...} }
(function (root, factory) {
    if (typeof module !== 'undefined' && module.exports) module.exports = factory();
    else root.HSEvents = factory();
})(typeof self !== 'undefined' ? self : this, function () {
    'use strict';

    // Index of the newest full generated set in the transcript, or -1.
    function latestSetPos(events) {
        for (let i = events.length - 1; i >= 0; i--) {
            if (events[i] && events[i].kind === 'hypotheses') return i;
        }
        return -1;
    }

    // Index of the newest set-defining event: a full generated set, or a
    // 'removal' event (which carries the re-indexed surviving set snapshot).
    function latestSetDefiningPos(events) {
        for (let i = (events || []).length - 1; i >= 0; i--) {
            const ev = events[i];
            if (!ev) continue;
            if (ev.kind === 'hypotheses') return i;
            if (ev.kind === 'removal' && Array.isArray(ev.hypotheses)) return i;
        }
        return -1;
    }

    // The "active version" of each hypothesis lineage in the LATEST generated set:
    //   { [index]: { eventId, hypothesis } }
    // The latest set-defining event (full set, or a removal carrying the
    // re-indexed surviving set) defines the current lineages; any 'revision'
    // event that appears AFTER it supersedes its hypothesis for its index
    // (newest revision wins). Earlier sets and their revisions are
    // historical/read-only and never appear here. The original set's hypotheses
    // array is never mutated — revisions are appended as separate events, so
    // this only reads.
    function computeActiveVersions(events) {
        const active = {};
        const setPos = latestSetDefiningPos(events || []);
        if (setPos === -1) return active;
        const setEv = events[setPos];
        (setEv.hypotheses || []).forEach(function (h) {
            active[h.index] = { eventId: setEv.id, hypothesis: h };
        });
        for (let i = setPos + 1; i < events.length; i++) {
            const ev = events[i];
            if (ev && ev.kind === 'revision' && ev.hypothesis && ev.index != null) {
                active[ev.index] = { eventId: ev.id, hypothesis: ev.hypothesis };
            }
        }
        return active;
    }

    // Is the hypothesis row rendered from (eventId, index) the active version of
    // its lineage? Only the active version is interactive (revise / edit / select).
    function isActiveVersion(events, eventId, index) {
        const active = computeActiveVersions(events);
        return !!(active[index] && active[index].eventId === eventId);
    }

    // The active hypothesis object for an index (or null) — used by selection and
    // by follow-on revisions so they always operate on the newest version.
    function activeHypothesis(events, index) {
        const active = computeActiveVersions(events);
        return active[index] ? active[index].hypothesis : null;
    }

    // Ordered, slot-local results for the latest candidate set. A result is
    // created only by the full generated set or an individual regeneration.
    // Ordinary edits update the currently active result without adding another
    // counter entry. Editing a historical result moves the active pointer to
    // that result, preserving every later regeneration result.
    function regenerationHistory(events, index) {
        const idx = Number(index);
        const setPos = latestSetPos(events || []);
        if (!idx || setPos === -1) return { results: [], activePosition: -1 };

        // Track candidate lineages as objects so a removal can splice one slot
        // and reindex the survivors without replacing or truncating their
        // regeneration histories.
        const setEvent = events[setPos] || {};
        const lineages = [];
        (setEvent.hypotheses || []).forEach(function (h, position) {
            const slot = Number(h && h.index) || position + 1;
            lineages[slot - 1] = {
                results: [{
                    eventId: setEvent.id || null,
                    hypothesis: h,
                    threadRecoverable: !!(h && h.thread_recoverable),
                }],
                activePosition: 0,
            };
        });

        for (let i = setPos + 1; i < events.length; i++) {
            const ev = events[i];
            if (!ev) continue;
            if (ev.kind === 'removal') {
                const removedIndex = Number(ev.index);
                if (removedIndex > 0 && removedIndex <= lineages.length) {
                    lineages.splice(removedIndex - 1, 1);
                    // The removal snapshot contains the current, re-indexed
                    // cards. Update only each surviving lineage's active card.
                    (ev.hypotheses || []).forEach(function (h, position) {
                        const slot = Number(h && h.index) || position + 1;
                        const lineage = lineages[slot - 1];
                        if (!lineage || !lineage.results[lineage.activePosition]) return;
                        const current = lineage.results[lineage.activePosition];
                        lineage.results[lineage.activePosition] = {
                            eventId: current.eventId,
                            hypothesis: h,
                            threadRecoverable: h.thread_recoverable !== undefined
                                ? !!h.thread_recoverable
                                : current.threadRecoverable,
                        };
                    });
                }
                continue;
            }
            if (ev.kind !== 'revision' || !ev.hypothesis) continue;
            const eventIndex = Number(ev.index);
            const lineage = eventIndex > 0 ? lineages[eventIndex - 1] : null;
            if (!lineage) continue;
            if (ev.source === 'regenerated') {
                // Older generated-set events did not store a thread id. The
                // regeneration's before-snapshot is the authoritative final
                // snapshot of that prior result, so recover its availability
                // without exposing the thread id to the browser.
                if (lineage.results[lineage.activePosition] && ev.before && ev.before.thread_recoverable) {
                    lineage.results[lineage.activePosition] = {
                        eventId: lineage.results[lineage.activePosition].eventId,
                        hypothesis: lineage.results[lineage.activePosition].hypothesis,
                        threadRecoverable: true,
                    };
                }
                lineage.results.push({
                    eventId: ev.id || null,
                    hypothesis: ev.hypothesis,
                    threadRecoverable: !!ev.hypothesis.thread_recoverable,
                });
                lineage.activePosition = lineage.results.length - 1;
                continue;
            }
            if (ev.source === 'historical-edit') {
                const requestedId = ev.sourceResultEventId || null;
                const requestedById = requestedId
                    ? lineage.results.findIndex(function (result) { return result.eventId === requestedId; })
                    : -1;
                const requested = Number(ev.regenerationPosition);
                if (requestedById >= 0) {
                    lineage.activePosition = requestedById;
                } else if (Number.isInteger(requested) && requested >= 0 && requested < lineage.results.length) {
                    lineage.activePosition = requested;
                }
            }
            // Revisions do not create regeneration versions. They update the
            // result currently in use, including the fresh state_id returned
            // when an earlier result is chosen.
            if (lineage.activePosition >= 0 && lineage.results[lineage.activePosition]) {
                lineage.results[lineage.activePosition] = {
                    eventId: lineage.results[lineage.activePosition].eventId,
                    hypothesis: ev.hypothesis,
                    threadRecoverable: ev.hypothesis.thread_recoverable !== undefined
                        ? !!ev.hypothesis.thread_recoverable
                        : lineage.results[lineage.activePosition].threadRecoverable,
                };
            }
        }

        const current = lineages[idx - 1];
        return current || { results: [], activePosition: -1 };
    }

    function normText(value) {
        return String(value == null ? '' : value).replace(/\s+/g, ' ').trim();
    }

    function hypothesisTexts(h) {
        if (!h) return [];
        return [h.full_text, h.formatted_output, h.hypothesis]
            .map(normText)
            .filter(Boolean);
    }

    function sameHypothesis(candidate, selected) {
        if (!candidate || !selected) return false;
        if (candidate.state_id && selected.state_id && candidate.state_id === selected.state_id) return true;
        if (candidate.index != null && selected.index != null && Number(candidate.index) !== Number(selected.index)) return false;
        const candidateTexts = hypothesisTexts(candidate);
        const selectedTexts = hypothesisTexts(selected);
        return selectedTexts.some(function (s) {
            return candidateTexts.some(function (c) { return c === s; });
        });
    }

    function findSelectionSource(events, selectionEvent) {
        if (!selectionEvent || !selectionEvent.hypothesis) return null;
        const selected = selectionEvent.hypothesis;
        const stop = events.indexOf(selectionEvent);
        const end = stop >= 0 ? stop - 1 : (events.length - 1);
        let source = null;
        for (let i = end; i >= 0; i--) {
            const ev = events[i];
            if (!ev) continue;
            if (ev.kind === 'revision' && sameHypothesis(ev.hypothesis, selected)) {
                source = { eventId: ev.id, index: ev.index, key: ev.id + ':' + ev.index };
                break;
            }
            if (ev.kind === 'hypotheses' || (ev.kind === 'removal' && Array.isArray(ev.hypotheses))) {
                const hyps = ev.hypotheses || [];
                for (let j = hyps.length - 1; j >= 0; j--) {
                    if (sameHypothesis(hyps[j], selected)) {
                        source = { eventId: ev.id, index: hyps[j].index, key: ev.id + ':' + hyps[j].index };
                        break;
                    }
                }
                if (source) break;
            }
        }
        if (!source) return null;

        // A selection belongs to a card lineage, not forever to the numeric
        // slot it occupied at selection time. Walk every later removal in
        // chronological order. Prefer an exact stable/content match when one
        // remains available; otherwise reindex the known lineage arithmetically.
        // The fallback is essential when an LLM edit that began before
        // selection completes afterward and rotates the card's version id.
        let currentIndex = Number(source.index);
        let currentEventId = source.eventId;
        if (stop >= 0 && Number.isInteger(currentIndex) && currentIndex > 0) {
            for (let i = stop + 1; i < events.length; i++) {
                const ev = events[i];
                if (!ev || ev.kind !== 'removal' || !Array.isArray(ev.hypotheses)) continue;
                const exact = ev.hypotheses.find(function (candidate) {
                    return sameHypothesis(candidate, selected);
                });
                if (exact) {
                    currentIndex = Number(exact.index);
                } else {
                    const removedIndex = Number(ev.index);
                    if (removedIndex === currentIndex) return null;
                    if (removedIndex > 0 && removedIndex < currentIndex) currentIndex -= 1;
                }
                currentEventId = ev.id || currentEventId;
            }
        }
        if (!Number.isInteger(currentIndex) || currentIndex < 1) return null;
        return {
            eventId: currentEventId,
            index: currentIndex,
            key: currentEventId + ':' + currentIndex,
        };
    }

    function latestSelection(events) {
        for (let i = (events || []).length - 1; i >= 0; i--) {
            if (events[i] && events[i].kind === 'selection') return events[i];
        }
        return null;
    }

    // A few older/live transcript paths could expose the LLM's complete edit
    // result as an assistant turn as well as storing it in the revision event.
    // Version-producing edits have one source of truth for that content: the
    // new hypothesis card. Suppress only exact full-result echoes; ordinary
    // discussion replies (including the concise "Created Version N." receipt)
    // remain in the source version's conversation.
    function revisionAssistantEchoes(events) {
        const echoes = {};
        (events || []).forEach(function (ev) {
            if (!ev || ev.kind !== 'revision' || ev.source !== 'regenerated' || !ev.hypothesis) return;
            const idx = Number(ev.index);
            if (!idx) return;
            const values = [ev.hypothesis.full_text, ev.hypothesis.formatted_output]
                .map(normText)
                .filter(Boolean);
            if (!values.length) return;
            const bucket = echoes[idx] || (echoes[idx] = new Set());
            values.forEach(function (value) { bucket.add(value); });
        });
        return echoes;
    }

    function isRevisionAssistantEcho(ev, echoes) {
        if (!ev || ev.kind !== 'message' || ev.role !== 'assistant') return false;
        const bucket = echoes[Number(ev.hypothesisIndex)];
        return !!(bucket && bucket.has(normText(ev.content)));
    }

    function computeThreads(events) {
        const threads = {};
        const revisionEchoes = revisionAssistantEchoes(events);
        const push = function (rawIndex, role, content) {
            const idx = Number(rawIndex);
            if (!idx) return;
            (threads[idx] = threads[idx] || []).push({ role: role, content: content });
        };
        (events || []).forEach(function (ev) {
            if (!ev) return;
            if (ev.kind === 'message' && ev.hypothesisIndex != null) {
                if (isRevisionAssistantEcho(ev, revisionEchoes)) return;
                push(ev.hypothesisIndex, ev.role === 'assistant' ? 'assistant' : 'user', ev.content || '');
            } else if (ev.kind === 'revision') {
                const idx = Number(ev.index);
                if (!idx) return;
                if (ev.source === 'regenerated') threads[idx] = [];
            } else if (ev.kind === 'removal') {
                const removedIndex = Number(ev.index);
                if (!removedIndex) return;
                const remapped = {};
                Object.keys(threads).forEach(function (key) {
                    const oldIndex = Number(key);
                    if (oldIndex < removedIndex) remapped[oldIndex] = threads[key];
                    else if (oldIndex > removedIndex) remapped[oldIndex - 1] = threads[key];
                });
                Object.keys(threads).forEach(function (key) { delete threads[key]; });
                Object.keys(remapped).forEach(function (key) { threads[key] = remapped[key]; });
            }
        });
        return threads;
    }

    // Discussion for one durable regeneration result. New histories associate
    // messages with a safe result-event identity; legacy histories fall back to
    // the prior index reducer without ever borrowing another known result.
    function regenerationThread(events, index, resultEventId) {
        const idx = Number(index);
        if (!idx) return [];
        const revisionEchoes = revisionAssistantEchoes(events);
        const setPos = latestSetPos(events || []);
        if (setPos === -1) return [];
        const lineages = [];
        ((events[setPos] || {}).hypotheses || []).forEach(function (h, position) {
            const slot = Number(h && h.index) || position + 1;
            lineages[slot - 1] = { identified: [] };
        });
        for (let i = setPos + 1; i < events.length; i++) {
            const ev = events[i];
            if (!ev) continue;
            if (ev.kind === 'removal') {
                const removedIndex = Number(ev.index);
                if (removedIndex > 0 && removedIndex <= lineages.length) lineages.splice(removedIndex - 1, 1);
                continue;
            }
            const eventIndex = ev.kind === 'message' ? Number(ev.hypothesisIndex) : Number(ev.index);
            const lineage = eventIndex > 0 ? lineages[eventIndex - 1] : null;
            if (!lineage || !ev.resultEventId) continue;
            if (ev.kind === 'message') {
                if (isRevisionAssistantEcho(ev, revisionEchoes)) continue;
                lineage.identified.push(ev);
            }
        }
        const target = lineages[idx - 1];
        const identified = target ? target.identified : [];
        if (resultEventId && identified.length) {
            return identified
                .filter(function (ev) { return ev.resultEventId === resultEventId; })
                .map(function (ev) {
                    return { role: ev.role === 'assistant' ? 'assistant' : 'user', content: ev.content || '' };
                });
        }
        return computeThreads(events)[idx] || [];
    }

    const COUNT_WORDS = {
        one: 1, two: 2, three: 3, four: 4, five: 5,
        six: 6, seven: 7, eight: 8, nine: 9, ten: 10,
    };

    function parseGenerationRequest(message, opts) {
        opts = opts || {};
        const min = opts.min || 1;
        const max = opts.max || 10;
        const text = String(message || '').toLowerCase().trim();
        if (!text) return null;
        if (/[?？]\s*$/.test(text)) return null;
        if (/\b(do not|don't|dont|should not|shouldn't|cannot|can't)\s+(?:\w+\s+){0,3}(generate|create|make|propose|draft)\b/.test(text)) return null;

        const commandMatch = text.match(/^(?:please\s+|kindly\s+)?(generate|create|make|propose|draft)\s+([\s\S]+)$/);
        if (!commandMatch) return null;

        const countToken = Object.keys(COUNT_WORDS).join('|') + '|10|[1-9]';
        const targetPattern = new RegExp(
            '^(?:(' + countToken + ')\\s+)?' +
            '(?:(?:new|fresh|more|additional|candidate|proposed)\\s+){0,4}' +
            '(hypothesis|hypotheses|research\\s+directions?|directions?)\\b' +
            '([\\s\\S]*)$'
        );
        const targetMatch = commandMatch[2].match(targetPattern);
        if (!targetMatch) return null;

        let count = null;
        if (targetMatch[1]) {
            count = COUNT_WORDS[targetMatch[1]] || Number(targetMatch[1]);
            if (!Number.isInteger(count) || count < min || count > max) count = null;
        }
        let guidance = (targetMatch[3] || '').trim();
        guidance = guidance.replace(/^[\s,.;:—–-]+/, '').trim();
        return { count: count, guidance: guidance || null };
    }

    // Normalize the backend's read-only history response into the browser
    // transcript vocabulary. Accepts both the current frontend-facing transcript
    // aliases (message / hypotheses / selection) and the durable backend event
    // type names (user_message / hypothesis_set_generated / hypothesis_selected)
    // so live restore and historical restore use one path.
    function normalizeHistoryEvents(data, nextId) {
        const out = [];
        const id = typeof nextId === 'function'
            ? nextId
            : function () { return 'e' + (out.length + 1); };
        const transcript = data && Array.isArray(data.transcript) ? data.transcript : null;

        if (transcript) {
            transcript.forEach(function (item) {
                if (!item) return;
                const kind = item.kind || item.type;
                if (kind === 'message' || kind === 'user_message' || kind === 'assistant_message') {
                    const role = kind === 'assistant_message' || item.role === 'assistant' ? 'assistant' : 'user';
                    const msg = { kind: 'message', role: role, content: item.content || '' };
                    if (item.phase) msg.phase = item.phase;
                    if (item.hypothesis_index != null) msg.hypothesisIndex = item.hypothesis_index;
                    if (item.result_event_id) msg.resultEventId = item.result_event_id;
                    out.push(msg);
                } else if (kind === 'hypotheses' || kind === 'hypothesis_set_generated') {
                    out.push({
                        kind: 'hypotheses',
                        id: item.result_event_id || id(),
                        hypotheses: item.hypotheses || [],
                        generationId: item.generation_id || item.generationId || null,
                    });
                } else if (kind === 'hypothesis_revised' || kind === 'hypothesis_regenerated' || kind === 'revision') {
                    const hyp = item.hypothesis || item.after || {};
                    const revision = {
                        kind: 'revision',
                        id: item.result_event_id || id(),
                        index: item.index != null ? item.index : (item.hypothesis_index != null ? item.hypothesis_index : hyp.index),
                        source: kind === 'hypothesis_regenerated'
                            ? 'regenerated'
                            : (item.action === 'edit_regeneration_version'
                                ? 'historical-edit'
                                : (item.source || 'revised')),
                        hypothesis: hyp,
                        before: item.before || null,
                    };
                    if (item.result_event_id) revision.resultEventId = item.result_event_id;
                    if (item.regeneration_position != null) {
                        revision.regenerationPosition = Number(item.regeneration_position);
                    }
                    if (item.source_result_event_id) {
                        revision.sourceResultEventId = item.source_result_event_id;
                    }
                    out.push(revision);
                } else if (kind === 'removal' || kind === 'hypothesis_removed') {
                    out.push({
                        kind: 'removal',
                        id: item.result_event_id || id(),
                        index: item.index != null ? item.index : item.hypothesis_index,
                        removed: item.removed || null,
                        hypotheses: Array.isArray(item.hypotheses) ? item.hypotheses : [],
                    });
                } else if (kind === 'selection' || kind === 'hypothesis_selected') {
                    out.push({ kind: 'selection', id: id(), hypothesis: item.hypothesis || {} });
                }
            });
            return out;
        }

        if (data && Array.isArray(data.generated_hypotheses) && data.generated_hypotheses.length) {
            out.push({ kind: 'hypotheses', id: id(), hypotheses: data.generated_hypotheses, generationId: null });
        }
        if (data && data.selected_hypothesis) {
            out.push({ kind: 'selection', id: id(), hypothesis: data.selected_hypothesis });
        }
        return out;
    }

    return {
        latestSetPos: latestSetPos,
        latestSetDefiningPos: latestSetDefiningPos,
        computeActiveVersions: computeActiveVersions,
        isActiveVersion: isActiveVersion,
        activeHypothesis: activeHypothesis,
        regenerationHistory: regenerationHistory,
        latestSelection: latestSelection,
        computeThreads: computeThreads,
        regenerationThread: regenerationThread,
        findSelectionSource: findSelectionSource,
        normalizeHistoryEvents: normalizeHistoryEvents,
        parseGenerationRequest: parseGenerationRequest,
    };
});
