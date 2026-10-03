import { useState, useEffect, useCallback, useRef } from 'react'
import { apiUrl, apiHeaders } from './config.js'
import './App.css'

/* ─────────────────────────────────────────────────────
   Helpers: map pipeline output → UI card data
   ───────────────────────────────────────────────────── */

const VERDICT_MAP = {
  supported: { label: 'Supported', cardStatus: 'success', group: 'correct' },
  unsupported: { label: 'Not supported', cardStatus: 'danger', group: 'attention' },
  uncertain: { label: 'Unclear', cardStatus: 'warning', group: 'attention' },
}

// Two distinct "not verified" sub-states, each with its own colour/icon so
// a suspicious reference doesn't look the same as a plain access problem:
//   - unresolved:   the DOI/reference itself can't be found anywhere — the
//                    citation may be fabricated or mis-numbered.
//   - inaccessible: the DOI/reference is real, but we couldn't obtain
//                    readable text for it (not indexed, not open access, etc).
const STATUS_MAP = {
  doi_not_found: { label: 'Reference not found', cardStatus: 'unresolved', group: 'pending' },
  reference_not_in_list: { label: 'Reference not found', cardStatus: 'unresolved', group: 'pending' },
  source_not_indexed: { label: 'Source unavailable', cardStatus: 'inaccessible', group: 'pending' },
  source_could_not_be_opened: { label: 'Source unavailable', cardStatus: 'inaccessible', group: 'pending' },
  source_abstained: { label: 'Source withheld (ambiguous)', cardStatus: 'inaccessible', group: 'pending' },
  technical_failure: { label: 'System error', cardStatus: 'technical', group: 'pending' },
}

const ACCESS_MAP = {
  full_text: 'Full text reviewed',
  abstract: 'Abstract only',
}

// Reuses the exact same cardStatus vocabulary (danger/warning/success/
// unresolved/technical) that ResultCard's CSS already defines -- so
// bibliography entries look visually consistent with sentence verdicts
// without a second color system.
const BIBLIOGRAPHY_STATUS_MAP = {
  well_cited: { label: 'Well cited', cardStatus: 'success' },
  miscited: { label: 'Miscited', cardStatus: 'warning' },
  synthesized_title: { label: 'Title not verbatim', cardStatus: 'warning' },
  wrong_article: { label: 'Wrong article', cardStatus: 'danger' },
  not_found: { label: 'Reference not found', cardStatus: 'unresolved' },
  technical_failure: { label: 'Could not verify', cardStatus: 'technical' },
}

const CHECK_NAMES = {
  1: 'Ambiguous citation keys',
  2: 'Partition coherence',
  3: 'Declared count vs references listed',
  4: 'Same figure cited differently',
  5: 'Year-volume coherence',
  7: 'Identifier resolves to wrong article',
  8: 'Displaced authorship',
  9: 'Synthesized title',
  10: 'Journal substitution',
  11: 'Volume out of range',
  13: 'Citation markers vs bibliography',
  14: 'Authors omitted without et al.',
  15: 'Retracted paper',
  16: 'Declared vs actual year',
}

function esc(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

function downloadReportJson(report) {
  const blob = new Blob([JSON.stringify(report, null, 2)], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `citation-report-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-')}.json`
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}

// Downloads the raw LLM call log straight from the server. A plain <a href>
// GET can't carry the X-API-Key header, so this goes through fetch + Blob
// instead, same as the authenticated POST calls elsewhere in this file.
// Exists specifically for Render's free tier, which has no persistent disk
// and no file browser -- this is the only practical way to pull
// output/llm_call_log.jsonl off a live instance after hitting a bug.
async function downloadLlmCallLog() {
  const res = await fetch(apiUrl('/api/download-log'), { headers: apiHeaders() })

  if (res.status === 404) {
    throw new Error('No log file on the server yet — run an analysis first, then try again.')
  }
  if (!res.ok) {
    throw new Error(`Could not download the log (server said ${res.status}).`)
  }

  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = 'llm_call_log.jsonl'
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}

function diagIcon(status) {
  if (status === 'success') return '✓'
  if (status === 'warning') return '?'
  if (status === 'technical') return '×'
  if (status === 'pending') return '…'
  if (status === 'unresolved') return '⊗'
  if (status === 'inaccessible') return '⊘'
  return '!'
}

/**
 * Transform the pipeline report into the card shape the UI expects.
 */
function transformReport(report) {
  const cards = []
  let cardId = 0

  for (const sentence of report.sentences) {
    cardId++
    const id = String(cardId).padStart(2, '0')

    // Find the worst verdict across pairs that reached a content verdict
    const verdictPairs = sentence.pairs.filter(p => p.status === 'verdict')
    const worstVerdict = sentence.verdict // already worst-of from aggregator

    // Determine the sentence-level card appearance
    let cardStatus, group, verdictLabel, accessLabel

    if (worstVerdict) {
      const v = VERDICT_MAP[worstVerdict]
      cardStatus = v.cardStatus
      group = v.group
      verdictLabel = v.label
    } else if (sentence.pairs.length === 1 && sentence.pairs[0].status === 'no_citations_found') {
      // No citation markers at all
      cardStatus = 'pending'
      group = 'pending'
      verdictLabel = 'Not verified'
    } else {
      // Some pairs failed before reaching a verdict
      const failedPair = sentence.pairs.find(p => p.status !== 'verdict' && p.status !== 'no_citations_found')
      if (failedPair) {
        const sm = STATUS_MAP[failedPair.status] || { label: 'Verification pending', cardStatus: 'pending', group: 'pending' }
        cardStatus = sm.cardStatus
        group = sm.group
        verdictLabel = sm.label
      } else {
        cardStatus = 'pending'
        group = 'pending'
        verdictLabel = 'Not verified'
      }
    }

    // Access label from best available source_type
    const bestPair = verdictPairs[0] || sentence.pairs.find(p => p.source_type)
    accessLabel = bestPair ? (ACCESS_MAP[bestPair.source_type] || 'Source checked') : 'No claim verdict'

    // Build evidence array from verdict pairs
    const evidence = verdictPairs.map(pair => {
      let relation = 'supports'
      if (pair.verdict === 'unsupported') relation = 'contradicts'
      else if (pair.verdict === 'uncertain') relation = null

      const sourceLabel = `Source [${pair.ref_number}]` + (pair.source_type === 'abstract' ? ' · abstract' : '')
      let html = pair.evidence_quote ? esc(pair.evidence_quote) : esc(pair.explanation || '')

      return { source: sourceLabel, relation, html }
    })

    // Collect DOIs
    const dois = sentence.pairs
      .filter(p => p.doi)
      .map(p => {
        if (p.status !== 'verdict') {
          const sm = STATUS_MAP[p.status]
          if (sm && p.status === 'doi_not_found') return `${p.doi} · not found`
        }
        return p.doi
      })

    // Build refs string
    const refNumbers = sentence.pairs
      .filter(p => p.ref_number != null)
      .map(p => p.ref_number)
    const refs = refNumbers.length > 0
      ? `[${refNumbers.join('][')}]`
      : ''

    // Build summary / issue text
    let summary = ''
    let issueTitle = ''
    let issue = ''

    if (worstVerdict === 'supported') {
      summary = 'The source evidence backs the sentence.'
      issueTitle = 'The source supports this claim'
      issue = verdictPairs[0]?.explanation || 'The source content confirms the sentence is accurately supported.'
    } else if (worstVerdict === 'unsupported') {
      if (verdictPairs.length > 1) {
        const supports = verdictPairs.filter(p => p.verdict === 'supported')
        const contradicts = verdictPairs.filter(p => p.verdict === 'unsupported')
        if (supports.length > 0 && contradicts.length > 0) {
          summary = `Partially supported: some sources agree, but at least one contradicts.`
          issueTitle = 'Multiple sources with conflicting results'
        } else {
          summary = 'The cited source does not support the sentence.'
          issueTitle = 'The source contradicts or does not back this claim'
        }
      } else {
        summary = 'The cited source does not support the sentence.'
        issueTitle = 'The source does not back this claim'
      }
      issue = verdictPairs[0]?.explanation || 'The source content does not support the claim as stated.'
    } else if (worstVerdict === 'uncertain') {
      summary = 'The available evidence is ambiguous or incomplete.'
      issueTitle = 'The evidence is inconclusive'
      issue = verdictPairs[0]?.explanation || 'The source content could not clearly confirm or deny the claim.'
    } else {
      // No verdict reached
      const failedPair = sentence.pairs.find(p => p.status !== 'verdict' && p.status !== 'no_citations_found')
      if (failedPair) {
        switch (failedPair.status) {
          case 'doi_not_found':
            summary = 'The reference could not be matched to a real publication.'
            issueTitle = 'There is no publication to compare with the sentence'
            issue = failedPair.error_message || 'The DOI could not be found in Crossref, OpenAlex, or PubMed.'
            break
          case 'source_not_indexed':
            summary = 'The publication exists but is not indexed in PubMed.'
            issueTitle = 'The source is not in PubMed'
            issue = failedPair.error_message || 'This publication could not be found in PubMed Central or PubMed.'
            break
          case 'source_could_not_be_opened':
            summary = 'The publication exists, but no readable text was available.'
            issueTitle = 'Access prevented verification'
            issue = failedPair.error_message || 'The paper is not open access and no usable abstract is available.'
            break
          case 'source_abstained':
            summary = 'The retrieved sources conflicted, so no source was auto-matched.'
            issueTitle = 'Ambiguous source'
            issue = failedPair.error_message || 'The fetcher found conflicting candidates and abstained rather than guessing.'
            break
          case 'technical_failure':
            summary = 'The comparison service failed before reaching a verdict. This is a system problem, not a problem with your citation.'
            issueTitle = 'System error'
            issue = failedPair.error_message || 'No scientific verdict was assigned. Nothing is wrong with this citation — the source should be checked again later.'
            break
          default:
            summary = 'This sentence could not be fully verified.'
            issueTitle = 'Verification incomplete'
            issue = failedPair.error_message || 'One or more sources could not be checked.'
        }
      } else {
        summary = 'No citation markers were found to check.'
        issueTitle = 'No citations detected'
        issue = 'This sentence does not contain bracketed reference numbers [N].'
      }
    }

    // Suggested revision is suppressed until it is generated from the source
    // values (evidence_quote / omitted_contradicting_evidence). Echoing the
    // flawed sentence unchanged would neutralise the "not supported" flag --
    // worse than not offering a suggestion at all.
    const suggestion = null

    cards.push({
      id,
      sentenceId: sentence.sentence_id,
      group,
      status: cardStatus,
      verdict: verdictLabel,
      access: accessLabel,
      claim: sentence.sentence_text,
      refs,
      summary,
      issueTitle,
      issue,
      evidence,
      suggestion,
      dois: dois.length > 0 ? dois : undefined,
      canRecheck: sentence.pairs.length > 0 && sentence.pairs[0].status !== 'no_citations_found',
    })
  }

  return cards
}


/* ─────────────────────────────────────────────────────
   Example text
   ───────────────────────────────────────────────────── */

const EXAMPLE_TEXT = `Prior work has established that AI-generated summaries reliably preserve the fidelity of the original source [1]. Cortisol impairs muscle growth both by activating proteolysis and by reducing protein synthesis [3][8]. In white muscle, cysteamine supplementation downregulated the igf-1ra and igf-1rb receptors [4]. Fasting reduced myogenic factors such as myod2, myf5 and myf6 [7]. The mineralocorticoid receptor has no known physiological role in ray-finned fish, with osmoregulation attributed to the glucocorticoid receptor alone [11]. Cortisol treatment increased hepatic glycogen content by 240% relative to untreated fish [13]. Glucocorticoid receptor density in red muscle rises during the parr-smolt transformation [15]. Crowding stress activates the ubiquitin-proteasome system and apoptosis pathways early, with autophagy-lysosome activation following later [16]. Chronic cortisol elevation shifts muscle fibre composition toward a slow-twitch phenotype in teleosts [19].

References
[1] Chen L, et al. Fidelity of large language model summarization in biomedical text. 2024. https://doi.org/10.1234/lgm.2024.0091
[3] Aedo J, et al. Cortisol and muscle proteolysis in teleosts. Gen Comp Endocrinol. 2017. https://doi.org/10.1016/j.ygcen.2017.06.012
[4] Valenzuela C, et al. Cysteamine supplementation and the GH/IGF axis. Gen Comp Endocrinol. 2023. https://doi.org/10.1016/j.ygcen.2023.114301
[7] Seiliez I, et al. Fasting and myogenic regulatory factors. Am J Physiol Regul Integr Comp Physiol. 2015. https://doi.org/10.1152/ajpregu.00238.2015
[8] Cassidy A, et al. Protein synthesis rates under cortisol treatment. J Exp Biol. 2020. https://doi.org/10.1242/jeb.213041
[11] Sakamoto T, et al. A physiological role for MR signalling in fish. Sci Rep. 2018. https://doi.org/10.1038/s41598-018-36681-w
[13] Rojas D, et al. Metabolic remodelling after cortisol treatment. Comp Biochem Physiol A. 2021. https://doi.org/10.1016/j.cbpa.2021.110985
[15] McCormick S. Glucocorticoid receptor density during smoltification. 2019.
[16] Nunez-Acuna G, et al. Proteolytic systems under high stocking density. BMC Vet Res. 2020. https://doi.org/10.1186/s12917-020-02518-w
[19] Martinez P, et al. Muscle fibre composition under chronic cortisol. J Fish Biol. 2021. https://doi.org/10.1111/jfb.14822`


/* ─────────────────────────────────────────────────────
   Screen 1: Input
   ───────────────────────────────────────────────────── */

function ScreenInput({ text, onTextChange, error, onAnalyze }) {
  const [parseWarnings, setParseWarnings] = useState(null)
  const textareaRef = useRef(null)

  const wordCount = text.trim() ? text.trim().split(/\s+/).length : 0
  const canAnalyze = wordCount >= 5

  function handleExample() {
    onTextChange(EXAMPLE_TEXT)
    setParseWarnings(null)
    textareaRef.current?.focus()
  }

  async function handleAnalyze() {
    if (!canAnalyze) return
    onAnalyze(text)
  }

  return (
    <section className="screen-input shell">
      <div className="eyebrow">Citation check</div>
      <h1>Paste your text.<br />We check every citation against its source.</h1>
      <p className="input-lead">
        Paste your introduction, discussion or literature review — with the reference list at the end.
        We read each sentence that cites a source, find the publication, and compare what your
        sentence says against what the publication actually says. Nothing to upload.
      </p>

      <div className="composer">
        <label htmlFor="inputText">Text to check</label>
        <span className="hint">
          Keep the reference list at the end. That is where we read each DOI from.
        </span>
        <textarea
          ref={textareaRef}
          id="inputText"
          placeholder="Paste your text here, followed by your reference list…"
          value={text}
          onChange={e => onTextChange(e.target.value)}
        />
        <div className="composer-actions">
          <button className="btn" disabled={!canAnalyze} onClick={handleAnalyze}>
            Check citations
          </button>
          <button className="btn btn-ghost" onClick={handleExample}>Use example text</button>
          <span className="composer-note">
            {wordCount === 1 ? '1 word' : `${wordCount} words`}
          </span>
        </div>
      </div>

      {error && <div className="error-banner" role="alert">{error}</div>}
      {parseWarnings && parseWarnings.length > 0 && (
        <div className="parse-warnings">
          <h4>Parsing notes</h4>
          <ul>
            {parseWarnings.map((w, i) => <li key={i}>{w}</li>)}
          </ul>
        </div>
      )}

      <p className="scope-line">
        Biomedical MVP. Sources are checked against Crossref, OpenAlex and PubMed Central,
        in English. Reference details — author, journal, volume, DOI — are checked against
        the official record; whether a paper's declared inclusion criteria were actually
        applied is not.
      </p>
    </section>
  )
}


/* ─────────────────────────────────────────────────────
   Screen 2: Loading
   ───────────────────────────────────────────────────── */

const LOADING_STEPS = [
  'Finding the sentences that cite a source',
  'Resolving each reference to a real publication',
  'Looking for the full text, then the abstract',
  'Comparing each sentence against its source',
  'Building your report',
]

// Map the backend's live progress events onto the step list so the ticks
// reflect what the pipeline has actually finished, not a fixed timer.
function progressFromEvents(events) {
  const stages = new Set(events.map(e => e.stage))
  let done = 0
  if (events.length > 0) done = Math.max(done, 1)
  if (stages.has('ids')) done = Math.max(done, 2)
  if (stages.has('content')) done = Math.max(done, 3)
  if (stages.has('llm')) done = Math.max(done, 3)
  if (stages.has('verdict') || stages.has('system')) done = Math.max(done, 4)
  return done
}

function ScreenLoading({ events }) {
  const doneCount = progressFromEvents(events)
  const activeIndex = Math.min(doneCount, LOADING_STEPS.length - 1)
  const current = LOADING_STEPS[activeIndex]
  const pct = Math.round((doneCount / LOADING_STEPS.length) * 100)

  return (
    <section className="screen-loading shell">
      <div className="loading-grid">
        <div className="loading-left">
          <div className="eyebrow">Checking your text</div>
          <h1>This takes a minute or two.</h1>
          <p className="loading-note">Longer papers take longer — every sentence that cites a source is read individually.</p>
          <ul className="loading-steps">
            {LOADING_STEPS.map((label, i) => {
              let cls = ''
              if (i < doneCount) cls = 'done'
              else if (i === activeIndex) cls = 'active'
              return (
                <li key={i} className={cls}>
                  <i></i>
                  {label}
                </li>
              )
            })}
          </ul>
        </div>

        <div className="loading-right">
          <div className="ring-stage">
            <div className="ring" aria-hidden="true"></div>
            <div className="ring-core">
              <h1 key={current} className="ring-word">{current}</h1>
              <p className="ring-note">
                {events.length === 0 ? 'Waking up the checker service…' : `${pct}% · ${events.length} updates`}
              </p>
            </div>
          </div>
        </div>
      </div>
    </section>
  )
}


/* ─────────────────────────────────────────────────────
   Components for Screen 3: Report
   ───────────────────────────────────────────────────── */

const FILTER_LABELS = {
  all: 'All results',
  attention: 'Needs correction',
  correct: 'Correct',
  pending: 'Not verified',
}

function ResultCard({ card, onRecheck }) {
  const [open, setOpen] = useState(false)
  const [copied, setCopied] = useState(false)
  const [rechecking, setRechecking] = useState(false)
  const [recheckError, setRecheckError] = useState(null)

  function handleCopy() {
    if (!card.suggestion) return
    navigator.clipboard.writeText(card.suggestion).catch(() => {})
    setCopied(true)
    setTimeout(() => setCopied(false), 1800)
  }

  async function handleRecheck() {
    setRechecking(true)
    setRecheckError(null)
    try {
      await onRecheck(card.sentenceId)
    } catch (err) {
      setRecheckError(err.message || 'The recheck failed. Please try again.')
    } finally {
      setRechecking(false)
    }
  }

  return (
    <article className={`result-card ${card.status}`}>
      <button
        className="result-summary"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        aria-controls={`detail-${card.id}`}
      >
        <span className="result-index">{card.id}</span>
        <span className="result-main">
          <span className="result-meta">
            <span className="verdict-badge">{esc(card.verdict)}</span>
            <span className="access"><i></i>{esc(card.access)}</span>
          </span>
          <span className="claim-line">
            {esc(card.claim)} {card.refs && <b>{esc(card.refs)}</b>}
          </span>
          <span className="fast-diagnosis">
            <strong>Key finding:</strong> {esc(card.summary)}
          </span>
        </span>
        <span className="expand-control">
          <span>{open ? 'Hide evidence' : 'View evidence'}</span>
          <i>{open ? '−' : '+'}</i>
        </span>
      </button>

      {open && (
        <div className="result-detail" id={`detail-${card.id}`}>
          {card.evidence && card.evidence.length > 0 && (
            <div className="comparison">
              <section className="comparison-panel user-text">
                <div className="panel-label"><span>1</span> Your sentence</div>
                <p>{esc(card.claim)} <b>{esc(card.refs)}</b></p>
              </section>
              <section className="comparison-panel source-text">
                <div className="panel-label"><span>2</span> Source evidence</div>
                <div className="evidence-stack">
                  {card.evidence.map((ev, i) => (
                    <div className="evidence" key={i}>
                      <div className="evidence-head">
                        <strong>{esc(ev.source)}</strong>
                        {ev.relation && (
                          <span className={`relation ${ev.relation}`}>
                            {ev.relation === 'supports' ? '✓ Supports' : '× Contradicts'}
                          </span>
                        )}
                      </div>
                      <p dangerouslySetInnerHTML={{ __html: ev.html }} />
                    </div>
                  ))}
                </div>
              </section>
            </div>
          )}

          <div className={`diagnosis-box ${card.status}`}>
            <div className="diagnosis-icon">{diagIcon(card.status)}</div>
            <div>
              <span className="detail-label">Assessment</span>
              <h3>{esc(card.issueTitle)}</h3>
              <p>{esc(card.issue)}</p>
            </div>
          </div>

          {card.suggestion && (
            <div className="suggestion-box">
              <div className="suggestion-copy">
                <span className="detail-label">Suggested revision</span>
                <p>"{esc(card.suggestion)}"</p>
              </div>
              <button onClick={handleCopy}>
                {copied ? 'Copied ✓' : 'Copy revision'}
              </button>
            </div>
          )}

          {card.canRecheck && (
            <div className="retry-box">
              <div className="retry-copy">
                <span className="detail-label">Not sure, or something went wrong?</span>
                <p>Recheck this source. It runs the full comparison again and updates this result in place.</p>
                {recheckError && <p className="retry-error">{esc(recheckError)}</p>}
              </div>
              <button onClick={handleRecheck} disabled={rechecking}>
                {rechecking ? 'Rechecking…' : 'Recheck this source'}
              </button>
            </div>
          )}

          {card.dois && (
            <footer className="source-footer">
              <span>Sources</span>
              {card.dois.map((doi, i) => {
                const dead = doi.includes('not found')
                return dead
                  ? <a key={i} className="doi-link dead">{esc(doi)}</a>
                  : <a key={i} className="doi-link" href={`https://doi.org/${doi}`} target="_blank" rel="noreferrer">{esc(doi)}</a>
              })}
            </footer>
          )}
        </div>
      )}
    </article>
  )
}


function BibliographyEntry({ entry }) {
  const [open, setOpen] = useState(false)
  const statusInfo = BIBLIOGRAPHY_STATUS_MAP[entry.status] || { label: entry.status, cardStatus: 'pending' }
  const hasFindings = entry.findings && entry.findings.length > 0
  const usedCount = entry.used_in_sentences ? entry.used_in_sentences.length : 0

  return (
    <article className={`result-card ${statusInfo.cardStatus}`}>
      <button
        className="result-summary"
        onClick={() => hasFindings && setOpen(!open)}
        aria-expanded={open}
        disabled={!hasFindings}
      >
        <span className="result-index">[{entry.reference_id}]</span>
        <span className="result-main">
          <span className="result-meta">
            <span className="verdict-badge">{esc(statusInfo.label)}</span>
            {usedCount > 0 && (
              <span className="access">
                <i></i>Cited in {usedCount} sentence{usedCount === 1 ? '' : 's'}
              </span>
            )}
          </span>
          {entry.usable_identifier && (
            <span className="claim-line">
              <a
                className="doi-link"
                href={`https://doi.org/${entry.usable_identifier}`}
                target="_blank"
                rel="noreferrer"
                onClick={(e) => e.stopPropagation()}
              >
                {esc(entry.usable_identifier)}
              </a>
            </span>
          )}
        </span>
        {hasFindings && (
          <span className="expand-control">
            <span>{open ? 'Hide details' : `${entry.findings.length} finding${entry.findings.length === 1 ? '' : 's'}`}</span>
            <i>{open ? '−' : '+'}</i>
          </span>
        )}
      </button>

      {open && hasFindings && (
        <div className="result-detail">
          <div className="bib-findings">
            {entry.findings.map((f, i) => (
              <div className="bib-finding" key={i}>
                <span className={`bib-finding-severity ${f.severity}`}>{f.severity}</span>
                <div className="bib-finding-body">
                  {f.check_id != null && (
                    <span className="bib-finding-check">Check {f.check_id}{CHECK_NAMES[f.check_id] ? `: ${CHECK_NAMES[f.check_id]}` : ''} — </span>
                  )}
                  {f.field && <strong>{esc(f.field)}: </strong>}
                  {esc(f.message)}
                  {(f.declared || f.actual) && (
                    <div className="bib-finding-fields">
                      {f.declared && <span>Declared: {esc(f.declared)}</span>}
                      {f.actual && <span>Record: {esc(f.actual)}</span>}
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </article>
  )
}

function BibliographySection({ report }) {
  const bibliographyStatus = report.bibliography_status || []
  const documentLevelFindings = report.document_level_findings || []
  const notEvaluated = report.not_evaluated_checks || []

  if (bibliographyStatus.length === 0 && documentLevelFindings.length === 0 && !report.metadata_error) {
    return null
  }

  const problemCount = bibliographyStatus.filter(e => e.status !== 'well_cited').length

  return (
    <section className="results-section shell bibliography-section">
      <div className="results-heading">
        <h2>Reference citation check</h2>
        <p className="hero-copy">
          {problemCount > 0
            ? `${problemCount} of ${bibliographyStatus.length} reference${bibliographyStatus.length === 1 ? '' : 's'} may be miscited.`
            : `All ${bibliographyStatus.length} reference${bibliographyStatus.length === 1 ? '' : 's'} checked out.`}
          {' '}This checks the reference itself — author, journal, volume, DOI — separately from whether the
          claim matches the source above. A reference can be well cited even if its sentence needs attention,
          and vice versa.
        </p>
      </div>

      {report.metadata_error && (
        <div className="error-banner" role="alert">
          Reference citation checks could not be completed: {esc(report.metadata_error)}
        </div>
      )}

      {bibliographyStatus.length > 0 && (
        <div className="result-list">
          {bibliographyStatus.map(entry => (
            <BibliographyEntry key={entry.reference_id} entry={entry} />
          ))}
        </div>
      )}

      {documentLevelFindings.length > 0 && (
        <div className="bib-document-findings">
          <h3>Document-level issues</h3>
          <p className="scope-note-sub">Not tied to a single reference — e.g. a citation number with no matching entry in the reference list.</p>
          <div className="bib-findings">
            {documentLevelFindings.map((f, i) => (
              <div className="bib-finding" key={i}>
                <span className={`bib-finding-severity ${f.severity}`}>{f.severity}</span>
                <div className="bib-finding-body">
                  {f.check_id != null && (
                    <span className="bib-finding-check">Check {f.check_id}{CHECK_NAMES[f.check_id] ? `: ${CHECK_NAMES[f.check_id]}` : ''} — </span>
                  )}
                  {f.field && <strong>{esc(f.field)}: </strong>}
                  {esc(f.message)}
                  {(f.declared || f.actual) && (
                    <div className="bib-finding-fields">
                      {f.declared && <span>Declared: {esc(f.declared)}</span>}
                      {f.actual && <span>Record: {esc(f.actual)}</span>}
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {notEvaluated.length > 0 && (
        <aside className="scope-note">
          <span className="scope-icon">i</span>
          <div>
            <h3>Not yet checked</h3>
            {notEvaluated.map((c, i) => (
              <p key={i}>
                {c.check_id != null && <strong>Check {c.check_id}{CHECK_NAMES[c.check_id] ? ` (${CHECK_NAMES[c.check_id]})` : ''}: </strong>}
                {c.component && <span className="scope-note-sub">{esc(c.component)} — </span>}
                {esc(c.reason)}
              </p>
            ))}
          </div>
        </aside>
      )}
    </section>
  )
}


function ScreenReport({ report, onStartOver, onRecheck }) {
  const [filter, setFilter] = useState('all')
  const [logDownloadState, setLogDownloadState] = useState({ status: 'idle', message: '' })
  const cards = transformReport(report)
  const summary = report.summary

  const handleDownloadLog = async () => {
    setLogDownloadState({ status: 'downloading', message: '' })
    try {
      await downloadLlmCallLog()
      setLogDownloadState({ status: 'idle', message: '' })
    } catch (err) {
      setLogDownloadState({ status: 'error', message: err.message })
    }
  }

  const filteredCards = filter === 'all'
    ? cards
    : cards.filter(c => c.group === filter)

  const counts = {
    all: cards.length,
    attention: cards.filter(c => c.group === 'attention').length,
    correct: cards.filter(c => c.group === 'correct').length,
    pending: cards.filter(c => c.group === 'pending').length,
  }

  // Build hero text
  const flagged = counts.attention
  const totalSentences = summary.total_sentences

  let heroTitle = ''
  if (flagged > 0) {
    heroTitle = `${flagged} sentence${flagged === 1 ? '' : 's'} need your attention`
  } else {
    heroTitle = 'All sentences look good'
  }

  const flaggedPart = flagged > 0
    ? ' and flagged ' + flagged + '. Compare each flagged sentence with the source evidence, and apply the suggested revision to correct your text.'
    : ' and found no issues.'
  const heroDesc = 'We evaluated ' + totalSentences + ' sentence' + (totalSentences === 1 ? '' : 's') + flaggedPart

  // Build verdict legend
  const legendItems = []
  if (counts.correct > 0) {
    legendItems.push({
      dot: 'dot-supported',
      html: `<strong>${counts.correct} Supported sentence${counts.correct === 1 ? '' : 's'},</strong> which means the source evidence backs the sentence.`,
    })
  }
  // Count unclear within attention
  const unclearCount = cards.filter(c => c.verdict === 'Unclear').length
  if (unclearCount > 0) {
    legendItems.push({
      dot: 'dot-unclear',
      html: `<strong>${unclearCount} Unclear sentence${unclearCount === 1 ? '' : 's'},</strong> which means the available evidence is ambiguous or incomplete.`,
    })
  }
  const notSupportedCount = cards.filter(c => c.verdict === 'Not supported').length
  if (notSupportedCount > 0) {
    legendItems.push({
      dot: 'dot-notsupported',
      html: `<strong>${notSupportedCount} Not supported sentence${notSupportedCount === 1 ? '' : 's'},</strong> which means the source does not back the sentence or contradicts it.`,
    })
  }
  // Split "Not verified" into why it wasn't verified: a reference that
  // couldn't be found at all (potentially fabricated or mis-numbered) is a
  // different problem than a real reference we simply couldn't get text
  // for, so they get separate legend lines and colours.
  const unresolvedCount = cards.filter(c => c.status === 'unresolved').length
  if (unresolvedCount > 0) {
    legendItems.push({
      dot: 'dot-unresolved',
      html: `<strong>${unresolvedCount} Reference not found</strong> — the citation could not be matched to any publication.`,
    })
  }
  const inaccessibleCount = cards.filter(c => c.status === 'inaccessible').length
  if (inaccessibleCount > 0) {
    legendItems.push({
      dot: 'dot-inaccessible',
      html: `<strong>${inaccessibleCount} Source unavailable</strong> — the publication exists but no readable text could be obtained.`,
    })
  }
  const otherPendingCount = counts.pending - unresolvedCount - inaccessibleCount
  if (otherPendingCount > 0) {
    legendItems.push({
      dot: 'dot-notverified',
      html: `<strong>${otherPendingCount} Not verified sentence${otherPendingCount === 1 ? '' : 's'}</strong>`,
    })
  }

  return (
    <>
      <div className="report-nav shell">
        <button className="back-link" onClick={onStartOver}>← Check another text</button>
        <button className="btn btn-ghost" onClick={() => downloadReportJson(report)}>Download results (JSON)</button>
        <button
          className="btn btn-ghost"
          onClick={handleDownloadLog}
          disabled={logDownloadState.status === 'downloading'}
        >
          {logDownloadState.status === 'downloading' ? 'Downloading…' : 'Download LLM call log'}
        </button>
      </div>
      {logDownloadState.status === 'error' && (
        <div className="error-banner shell" role="alert">{logDownloadState.message}</div>
      )}

      <section className="hero shell">
        <div className="eyebrow">Citation review complete</div>
        <h1>{heroTitle}</h1>
        <p className="hero-copy">{heroDesc}</p>
        {legendItems.length > 0 && (
          <>
            <p className="found-label">We found:</p>
            <ul className="verdict-legend">
              {legendItems.map((item, i) => (
                <li key={i}>
                  <i className={item.dot}></i>
                  <span dangerouslySetInnerHTML={{ __html: item.html }} />
                </li>
              ))}
            </ul>
          </>
        )}
      </section>

      <nav className="filterbar" aria-label="Filter citation results">
        <div className="shell filterbar-inner">
          {Object.entries(FILTER_LABELS).map(([key, label]) => (
            <button
              key={key}
              className={`filter ${filter === key ? 'active' : ''}`}
              onClick={() => setFilter(key)}
            >
              {label}
              <span>{counts[key]}</span>
            </button>
          ))}
        </div>
      </nav>

      <section className="results-section shell">
        <div className="results-heading">
          {filter !== 'all' && (
            <button className="clear-filter" onClick={() => setFilter('all')}>
              Show all results
            </button>
          )}
        </div>

        <div className="result-list">
          {filteredCards.map(card => (
            <ResultCard key={card.id} card={card} onRecheck={onRecheck} />
          ))}
        </div>

        <aside className="scope-note">
          <span className="scope-icon">i</span>
          <div>
            <h3>What "Supported" means here</h3>
            <p>
              A sentence's verdict above is about whether the claim matches its source's content.
              Whether the reference itself is correctly cited — author, journal, volume, DOI — is
              checked separately below.
            </p>
          </div>
        </aside>
      </section>

      <BibliographySection report={report} />
    </>
  )
}


/* ─────────────────────────────────────────────────────
   App: screen routing
   ───────────────────────────────────────────────────── */

export default function App() {
  const [screen, setScreen] = useState('input') // 'input' | 'loading' | 'report'
  const [report, setReport] = useState(null)
  const [text, setText] = useState('')
  const [error, setError] = useState(null)
  const [liveEvents, setLiveEvents] = useState([])

  // Wake the backend as soon as the page loads. Render's free tier sleeps
  // after inactivity, and the cold start can take 30-60s; by pinging the
  // health endpoint now, the instance is usually warm by the time the user
  // pastes text and clicks "Check citations".
  useEffect(() => {
    fetch(apiUrl('/api/health'), { method: 'GET' }).catch(() => {})
  }, [])

  // A sleeping instance can drop the first request at the network level
  // before the cold start completes. Retry network-level failures with
  // backoff rather than giving up on the first try. Server-side errors and
  // timeouts are final and are not retried.
  const ANALYZE_RETRIES = 3
  const RETRY_DELAY_MS = [2000, 4000, 8000]
  const POLL_INTERVAL_MS = 1500

  // Poll a running job until it finishes, feeding live events into the
  // loading screen as they arrive. Transient poll failures are retried a few
  // times; the AbortController's 5-minute budget is the hard ceiling.
  const pollJob = async (jobId, controller) => {
    let pollFails = 0
    while (true) {
      let data
      try {
        const res = await fetch(apiUrl(`/api/progress/${jobId}`), { signal: controller.signal })
        data = await res.json()
        if (!res.ok) throw new Error(data.error || `Server returned ${res.status}`)
        pollFails = 0
      } catch (err) {
        if (err.name === 'AbortError') throw err
        pollFails += 1
        if (pollFails >= 5) throw new Error('Lost connection to the checker service. Please try again.')
        await new Promise(resolve => setTimeout(resolve, 3000))
        continue
      }
      if (data.events) setLiveEvents(data.events)
      if (data.status === 'done') return data.report
      if (data.status === 'error') throw new Error(data.error || 'The check failed on the server.')
      await new Promise(resolve => setTimeout(resolve, POLL_INTERVAL_MS))
    }
  }

  const handleAnalyze = useCallback(async (textToCheck) => {
    // Show the loading screen BEFORE the request so the user isn't staring at
    // a stuck button for minutes.
    setError(null)
    setLiveEvents([])
    setScreen('loading')

    let lastErr = null
    for (let attempt = 0; attempt <= ANALYZE_RETRIES; attempt++) {
      if (attempt > 0) {
        await new Promise(resolve => setTimeout(resolve, RETRY_DELAY_MS[attempt - 1]))
      }
      const controller = new AbortController()
      const timeout = setTimeout(() => controller.abort(), 5 * 60 * 1000)
      try {
        // Phase 1: parse the text and start the job (returns a job id).
        const res = await fetch(apiUrl('/api/analyze-text'), {
          method: 'POST',
          headers: apiHeaders({ 'Content-Type': 'application/json' }),
          body: JSON.stringify({ text: textToCheck }),
          signal: controller.signal,
        })
        const data = await res.json()
        if (!res.ok) throw new Error(data.error || `Server returned ${res.status}`)
        // Phase 2: poll for live events, then the finished report.
        const report = await pollJob(data.job_id, controller)
        clearTimeout(timeout)
        setReport(report)
        setScreen('report')
        return
      } catch (err) {
        clearTimeout(timeout)
        lastErr = err
        // Only retry network-level failures (server not reachable / cold
        // start). A real HTTP error, a timeout or a failed job is final.
        if (err.name !== 'AbortError' && err.message !== 'Failed to fetch') break
      }
    }

    const err = lastErr
    if (err.name === 'AbortError') {
      setError('The check took too long and timed out. Please try again — if it keeps timing out, split your text into smaller sections.')
    } else if (err.message === 'Failed to fetch') {
      setError('Could not reach the checker service. It may be waking up or temporarily unavailable. Please try again in a moment.')
    } else {
      setError(err.message)
    }
    setScreen('input')
  }, [pollJob])

  const handleStartOver = useCallback(() => {
    setReport(null)
    setError(null)
    setLiveEvents([])
    setScreen('input')
  }, [])

  const handleRecheck = useCallback(async (sentenceId) => {
    if (!report || !text) throw new Error('Nothing to recheck.')

    const controller = new AbortController()
    const timeout = setTimeout(() => controller.abort(), 5 * 60 * 1000)
    try {
      const res = await fetch(apiUrl('/api/recheck'), {
        method: 'POST',
        headers: apiHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ text, sentence_id: sentenceId }),
        signal: controller.signal,
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.error || `Server returned ${res.status}`)
      const updatedSentence = data.sentences && data.sentences[0]
      if (!updatedSentence) throw new Error('The recheck returned no updated result.')
      setReport(prev => prev ? {
        ...prev,
        sentences: prev.sentences.map(s =>
          s.sentence_id === updatedSentence.sentence_id ? updatedSentence : s
        ),
      } : prev)
    } catch (err) {
      if (err.name === 'AbortError') {
        throw new Error('The recheck took too long and timed out. Please try again.')
      }
      throw err
    } finally {
      clearTimeout(timeout)
    }
  }, [report, text])

  return (
    <main>
      {screen === 'input' && (
        <ScreenInput
          text={text}
          onTextChange={setText}
          error={error}
          onAnalyze={handleAnalyze}
        />
      )}
      {screen === 'loading' && <ScreenLoading events={liveEvents} />}
      {screen === 'report' && report && (
        <ScreenReport report={report} onStartOver={handleStartOver} onRecheck={handleRecheck} />
      )}
    </main>
  )
}
