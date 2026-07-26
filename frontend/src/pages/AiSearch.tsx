import { useState } from 'react';
import { Link } from 'wouter';
import {
  BookOpen, Clock3, Database, Search, SlidersHorizontal, Sparkles,
} from 'lucide-react';
import { Button } from '../components/Button';
import { EmptyState } from '../components/EmptyState';
import { Spinner, SpinnerCentered } from '../components/Spinner';
import { useMe, useRagSearch, useRagStatus, useSearchOptions } from '../lib/queries';
import type { RagSearchMode, RagSearchResult } from '../lib/api';
import { useT } from '../lib/i18n';
import styles from './AiSearch.module.css';

const LIMITS = [6, 12, 20];
const MODES: RagSearchMode[] = ['hybrid', 'semantic', 'lexical'];

function csv(value: string): string[] | undefined {
  const items = value.split(',').map((item) => item.trim()).filter(Boolean);
  return items.length ? items : undefined;
}

function resultLocation(result: RagSearchResult): string | null {
  if (result.page_start != null) {
    return result.page_end != null && result.page_end !== result.page_start
      ? `Pages ${result.page_start}–${result.page_end}`
      : `Page ${result.page_start}`;
  }
  return result.chapter || result.section || null;
}

function scoreLabel(result: RagSearchResult): string | null {
  if (typeof result.evidence_score === 'number' && result.evidence_score > 0) {
    const evidence = `${Math.round(result.evidence_score * 100)}% match`;
    return typeof result.reranker_rank === 'number'
      ? `${evidence} · neural #${result.reranker_rank}`
      : evidence;
  }
  if (typeof result.semantic_score === 'number') {
    return `${Math.round(result.semantic_score * 100)}% semantic`;
  }
  return null;
}

export function AiSearch() {
  const t = useT();
  const me = useMe().data;
  const enabled = !!me?.features?.rag_search && !me?.role?.anonymous;
  const status = useRagStatus(enabled);
  const search = useRagSearch();
  const options = useSearchOptions();

  const [query, setQuery] = useState('');
  const [mode, setMode] = useState<RagSearchMode>('hybrid');
  const [limit, setLimit] = useState(12);
  const [formats, setFormats] = useState<string[]>([]);
  const [authors, setAuthors] = useState('');
  const [tags, setTags] = useState('');
  const [includeAdjacent, setIncludeAdjacent] = useState(true);

  if (!enabled) {
    return (
      <section className={styles.container}>
        <h1 className={styles.title}>{t('AI search')}</h1>
        <EmptyState message={t('AI search is not available for this account.')} />
      </section>
    );
  }

  const toggleFormat = (format: string) => {
    setFormats((current) => current.includes(format)
      ? current.filter((item) => item !== format)
      : [...current, format]);
  };

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    const text = query.trim();
    if (text.length < 2) return;
    search.mutate({
      query: text,
      mode,
      limit,
      formats: formats.length ? formats : undefined,
      authors: csv(authors),
      tags: csv(tags),
      include_adjacent: includeAdjacent,
      max_chunks_per_book: 3,
    });
  };

  const ragStatus = status.data;
  const results = search.data?.results ?? [];

  return (
    <section className={styles.container}>
      <header className={styles.header}>
        <div>
          <h1 className={styles.title}>
            <Sparkles size={27} aria-hidden="true" focusable={false} />
            {t('AI search')}
          </h1>
          <p className={styles.subtitle}>
            {t('Search inside the full text of your indexed books, not only titles and metadata.')}
          </p>
        </div>
        {status.isLoading ? <Spinner size={20} /> : ragStatus && (
          <div className={ragStatus.ready ? styles.readyBadge : styles.warningBadge}>
            <Database size={15} aria-hidden="true" focusable={false} />
            {ragStatus.indexed_books}/{ragStatus.total_books} {t('books indexed')}
          </div>
        )}
      </header>

      {status.error && (
        <div className={styles.warning} role="alert">
          {status.error instanceof Error ? status.error.message : t('Could not read the RAG status.')}
        </div>
      )}


      {ragStatus && (
        <div className={styles.stats} aria-label={t('RAG index status')}>
          <div className={styles.stat}>
            <span className={styles.statValue}>{ragStatus.indexed_books}</span>
            <span className={styles.statLabel}>{t('Indexed books')}</span>
          </div>
          <div className={styles.stat}>
            <span className={styles.statValue}>{ragStatus.total_chunks.toLocaleString()}</span>
            <span className={styles.statLabel}>{t('Text chunks')}</span>
          </div>
          <div className={styles.stat}>
            <span className={styles.statValue}>{ragStatus.not_indexed_books}</span>
            <span className={styles.statLabel}>{t('Not indexed')}</span>
          </div>
          <div className={styles.stat}>
            <span className={styles.statValue}>{ragStatus.failed_jobs}</span>
            <span className={styles.statLabel}>{t('Failed jobs')}</span>
          </div>
          <div className={styles.statusMeta}>
            <span>
              {t('Embedding model')}: {ragStatus.model || t('Unknown model')}
            </span>
            <span>
              {t('Reranker model')}: {ragStatus.reranker_model || t('Not enabled')}
            </span>
            {ragStatus.last_sync_at && (
              <span>
                <Clock3 size={14} aria-hidden="true" focusable={false} />
                {new Date(ragStatus.last_sync_at).toLocaleString()}
              </span>
            )}
          </div>
        </div>
      )}

      <form className={styles.form} onSubmit={submit}>
        <label className={styles.queryField}>
          <span>{t('What do you want to find?')}</span>
          <textarea
            className={styles.textarea}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={t('Describe an idea, scene, fact, quote, technology, or topic…')}
            rows={4}
            maxLength={1000}
            required
          />
        </label>

        <div className={styles.controls}>
          <label className={styles.field}>
            <span>{t('Search mode')}</span>
            <select className={styles.select} value={mode}
              onChange={(event) => setMode(event.target.value as RagSearchMode)}>
              {MODES.map((value) => (
                <option key={value} value={value}>{t(value)}</option>
              ))}
            </select>
          </label>
          <label className={styles.field}>
            <span>{t('Result limit')}</span>
            <select className={styles.select} value={limit}
              onChange={(event) => setLimit(Number(event.target.value))}>
              {LIMITS.map((value) => <option key={value} value={value}>{value}</option>)}
            </select>
          </label>
        </div>

        <details className={styles.advanced}>
          <summary className={styles.advancedSummary}>
            <SlidersHorizontal size={16} aria-hidden="true" focusable={false} />
            {t('Filters')}
          </summary>
          <div className={styles.filterGrid}>
            <label className={styles.field}>
              <span>{t('Authors')}</span>
              <input className={styles.input} value={authors}
                onChange={(event) => setAuthors(event.target.value)}
                placeholder={t('Comma-separated names')} />
            </label>
            <label className={styles.field}>
              <span>{t('Tags')}</span>
              <input className={styles.input} value={tags}
                onChange={(event) => setTags(event.target.value)}
                placeholder={t('Comma-separated tags')} />
            </label>
            <div className={styles.formatField}>
              <span>{t('Formats')}</span>
              <div className={styles.formatList}>
                {(options.data?.formats ?? []).map((format) => (
                  <button key={format} type="button"
                    className={formats.includes(format) ? styles.formatChipActive : styles.formatChip}
                    aria-pressed={formats.includes(format)}
                    onClick={() => toggleFormat(format)}>
                    {format}
                  </button>
                ))}
              </div>
            </div>
            <label className={styles.checkbox}>
              <input type="checkbox" checked={includeAdjacent}
                onChange={(event) => setIncludeAdjacent(event.target.checked)} />
              <span>{t('Include neighboring text for context')}</span>
            </label>
          </div>
        </details>

        <div className={styles.searchActions}>
          <Button type="submit" disabled={search.isPending || query.trim().length < 2}>
            {search.isPending ? <Spinner size={16} /> : <Search size={16} aria-hidden="true" focusable={false} />}
            {search.isPending ? t('Searching…') : t('Search book contents')}
          </Button>
        </div>
      </form>

      <section className={styles.results} aria-live="polite" aria-busy={search.isPending}>
        {search.isPending ? (
          <SpinnerCentered size={34} />
        ) : search.error ? (
          <EmptyState message={search.error instanceof Error ? search.error.message : t('AI search failed.')} />
        ) : search.data && results.length === 0 ? (
          <EmptyState message={t('No matching passages were found.')} />
        ) : results.length > 0 ? (
          <>
            <div className={styles.resultsHeader}>
              <h2>{t('Sources')}</h2>
              <div className={styles.resultsSummary}>
                <span>
                  {search.data?.count} {t('results')}
                  {typeof search.data?.duration_ms === 'number'
                    ? ` · ${(search.data.duration_ms / 1000).toFixed(2)} s` : ''}
                </span>
                <span>
                  {t('Embedding')}: {search.data?.model || t('Unknown model')}
                  {search.data?.reranker_model
                    ? ` · ${t('Reranker')}: ${search.data.reranker_model}`
                    : ''}
                </span>
              </div>
            </div>
            <div className={styles.resultList}>
              {results.map((result) => {
                const location = resultLocation(result);
                const score = scoreLabel(result);
                return (
                  <article key={`${result.book_id}-${result.chunk_id}`} className={styles.card}>
                    <div className={styles.cardHeader}>
                      <div>
                        <h3 className={styles.cardTitle}>
                          <Link href={`/book/${result.book_id}`}>
                            {result.title || t('Untitled')}
                          </Link>
                        </h3>
                        <p className={styles.sourceMeta}>
                          {result.authors.length > 0 && <span>{result.authors.join(', ')}</span>}
                          {result.format && <span>{result.format}</span>}
                          {location && <span>{location}</span>}
                        </p>
                      </div>
                      {score && <span className={styles.score}>{score}</span>}
                    </div>

                    <p className={styles.excerpt}>{result.text || t('No excerpt available.')}</p>
                    {result.matched_terms.length > 0 && (
                      <p className={styles.sourceMeta}>
                        <span>{t('Matched')}: {result.matched_terms.join(', ')}</span>
                      </p>
                    )}

                    {(result.context_before || result.context_after) && (
                      <details className={styles.context}>
                        <summary>{t('Show surrounding context')}</summary>
                        <div className={styles.contextGrid}>
                          {result.context_before && (
                            <div>
                              <strong>{t('Before')}</strong>
                              <p>{result.context_before}</p>
                            </div>
                          )}
                          {result.context_after && (
                            <div>
                              <strong>{t('After')}</strong>
                              <p>{result.context_after}</p>
                            </div>
                          )}
                        </div>
                      </details>
                    )}

                    <div className={styles.cardFooter}>
                      <Link href={`/book/${result.book_id}`} className={styles.openBook}>
                        <BookOpen size={15} aria-hidden="true" focusable={false} />
                        {t('Open book')}
                      </Link>
                    </div>
                  </article>
                );
              })}
            </div>
          </>
        ) : null}
      </section>
    </section>
  );
}
