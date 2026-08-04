import { useEffect, useMemo, useState } from 'react';
import { Link } from 'wouter';
import {
  ArrowLeft, BookOpenCheck, CheckCircle2, Cloud, FileSearch, FileWarning, Play, Save,
  Server, ShieldCheck, Wifi,
} from 'lucide-react';
import {
  useDiscoverMoonReaderCaches, useMoonReaderSettings, useSaveMoonReaderSettings,
  useStartMoonReaderSync, useTestMoonReaderConnection,
} from '../lib/queries';
import type { MoonReaderSettingsUpdate } from '../lib/api';
import { ApiError } from '../lib/api';
import { Button } from '../components/Button';
import { Spinner, SpinnerCentered } from '../components/Spinner';
import { EmptyState } from '../components/EmptyState';
import { useT } from '../lib/i18n';
import styles from './MoonReaderSync.module.css';

function displayDate(value: string | null | undefined): string {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

export function MoonReaderSync() {
  const t = useT();
  const settingsQuery = useMoonReaderSettings();
  const saveSettings = useSaveMoonReaderSettings();
  const testConnection = useTestMoonReaderConnection();
  const discoverCaches = useDiscoverMoonReaderCaches();
  const startSync = useStartMoonReaderSync();

  const [enabled, setEnabled] = useState(false);
  const [baseUrl, setBaseUrl] = useState('');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [cachePath, setCachePath] = useState('');
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);

  const settings = settingsQuery.data;
  useEffect(() => {
    if (!settings) return;
    setEnabled(settings.enabled);
    setBaseUrl(settings.base_url);
    setUsername(settings.username);
    setCachePath(settings.cache_path);
  }, [settings]);

  const payload = (): MoonReaderSettingsUpdate => ({
    enabled,
    base_url: baseUrl.trim(),
    username: username.trim(),
    cache_path: cachePath.trim(),
    ...(password ? { password } : {}),
  });

  const syncBusy = settings?.sync_status === 'queued' || settings?.sync_status === 'running';
  const actionBusy = saveSettings.isPending || testConnection.isPending
    || discoverCaches.isPending || startSync.isPending;
  const error = saveSettings.error ?? testConnection.error ?? discoverCaches.error ?? startSync.error;
  const normalizedCachePath = cachePath.trim();
  const selectedDiscoveredPath = discoverCaches.data?.locations.some(
    (location) => location.path === normalizedCachePath,
  ) ?? false;
  const discoveryListDisabled = Boolean(normalizedCachePath) && !selectedDiscoveredPath;
  const summary = settings?.last_sync_summary;
  const summaryEntries = useMemo(() => [
    [t('Position files'), summary?.files_found ?? 0],
    [t('Matched books'), summary?.matched ?? 0],
    [t('Updated positions'), summary?.updated ?? 0],
    [t('Unchanged'), summary?.unchanged ?? 0],
  ] as const, [summary, t]);

  if (settingsQuery.isLoading) return <SpinnerCentered size={40} />;
  if (settingsQuery.error || !settings) {
    return (
      <main className={styles.container}>
        <EmptyState message={settingsQuery.error instanceof Error
          ? settingsQuery.error.message : t('Could not load Moon+ Reader settings.')} />
      </main>
    );
  }

  const onSave = (event: React.FormEvent) => {
    event.preventDefault();
    setMessage(null);
    saveSettings.mutate(payload(), {
      onSuccess: () => {
        setPassword('');
        setMessage({ ok: true, text: t('Moon+ Reader settings saved.') });
      },
    });
  };

  const onTest = () => {
    setMessage(null);
    testConnection.mutate(payload(), {
      onSuccess: (data) => setMessage({
        ok: true,
        text: data.test?.cache_found
          ? t('WebDAV connected. Found {count} Moon+ position files.', {
              count: data.test.position_files,
            })
          : cachePath.trim()
            ? t('WebDAV connected. The selected Moon+ cache folder was not found.')
            : t('WebDAV connected. Use Find Moon sync files to choose a folder.'),
      }),
    });
  };

  const onDiscover = () => {
    setMessage(null);
    discoverCaches.mutate(payload(), {
      onSuccess: (data) => {
        const count = data.locations.length;
        setMessage({
          ok: count > 0,
          text: count > 0
            ? t('Found {count} Moon+ sync folders. Select one below.', { count })
            : t('No Moon+ sync folders were found in the scanned WebDAV area.'),
        });
      },
    });
  };

  const onSync = () => {
    setMessage(null);
    saveSettings.mutate(payload(), {
      onSuccess: () => {
        setPassword('');
        startSync.mutate(undefined, {
          onSuccess: () => setMessage({ ok: true, text: t('Moon+ position sync queued.') }),
        });
      },
    });
  };

  return (
    <main className={styles.container}>
      <Link href="/account" className={styles.backLink}>
        <ArrowLeft size={15} aria-hidden="true" /> {t('Back to account')}
      </Link>

      <div className={styles.heading}>
        <div className={styles.headingIcon}><BookOpenCheck size={24} aria-hidden="true" /></div>
        <div>
          <h1 className={styles.title}>{t('Moon+ Reader sync')}</h1>
          <p className={styles.subtitle}>
            {t('Import Moon+ Reader WebDAV positions into your Calibre-Web reading progress.')}
          </p>
        </div>
      </div>

      <form className={styles.card} onSubmit={onSave}>
        <h2 className={styles.cardTitle}><Server size={17} aria-hidden="true" /> {t('WebDAV connection')}</h2>
        <label className={styles.toggle}>
          <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
          <span>
            <strong>{t('Enable Moon+ Reader position sync')}</strong>
            <small>{t('Synchronization is one-way from Moon+ Reader to Calibre-Web for now.')}</small>
          </span>
        </label>

        <div className={styles.field}>
          <label htmlFor="moon-url">{t('WebDAV URL')}</label>
          <input id="moon-url" className={styles.input} type="url" required
            value={baseUrl} onChange={(e) => { setBaseUrl(e.target.value); discoverCaches.reset(); setMessage(null); }}
            placeholder="http://192.168.31.150:18283/books/" />
        </div>

        <div className={styles.row}>
          <div className={styles.field}>
            <label htmlFor="moon-user">{t('Username')}</label>
            <input id="moon-user" className={styles.input} required
              value={username} onChange={(e) => { setUsername(e.target.value); discoverCaches.reset(); setMessage(null); }} />
          </div>
          <div className={styles.field}>
            <label htmlFor="moon-password">{t('Password')}</label>
            <input id="moon-password" className={styles.input} type="password"
              autoComplete="new-password" value={password}
              onChange={(e) => { setPassword(e.target.value); discoverCaches.reset(); setMessage(null); }}
              placeholder={settings.password_configured
                ? t('Configured — leave blank to keep') : t('Enter WebDAV password')} />
          </div>
        </div>

        <div className={styles.field}>
          <div className={styles.pathLabelRow}>
            <label htmlFor="moon-cache">{t('Moon+ cache path')}</label>
            <Button type="button" variant="ghost" onClick={onDiscover}
              disabled={actionBusy || syncBusy || !baseUrl.trim() || !username.trim()
                || (!settings.password_configured && !password)}>
              {discoverCaches.isPending
                ? <Spinner size={14} /> : <FileSearch size={14} aria-hidden="true" />}
              {t('Find Moon sync files')}
            </Button>
          </div>
          <input id="moon-cache" className={styles.input}
            value={cachePath} onChange={(e) => setCachePath(e.target.value)}
            placeholder="Moon/.Moon+/Cache" />
          <p className={styles.hint}>
            {t('Leave blank, search the WebDAV server, then select one discovered Moon+ folder.')}
          </p>

          {discoverCaches.data && (
            <div
              className={`${styles.discoveryPanel} ${discoveryListDisabled ? styles.discoveryPanelDisabled : ''}`}
              role="group"
              aria-label={t('Discovered Moon+ sync folders')}
              aria-disabled={discoveryListDisabled}
            >
              {discoverCaches.data.locations.length > 0 ? (
                <ul className={styles.discoveryList}>
                  {discoverCaches.data.locations.map((location) => (
                    <li key={location.path}>
                      <label>
                        <input type="radio" name="moon-cache-location"
                          checked={normalizedCachePath === location.path}
                          disabled={discoveryListDisabled}
                          onChange={() => { setCachePath(location.path); setMessage(null); }} />
                        <span>
                          <code>{location.path}</code>
                          <small>
                            {t('{count} position files', { count: location.position_files })}
                            {location.last_modified
                              ? ` · ${t('Newest: {date}', { date: displayDate(location.last_modified) })}` : ''}
                          </small>
                        </span>
                      </label>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className={styles.hint}>{t('No Moon+ sync folders found.')}</p>
              )}
              <p className={styles.scanMeta}>
                {t('Scanned {count} WebDAV folders up to {depth} levels deep.', {
                  count: discoverCaches.data.scanned_collections,
                  depth: discoverCaches.data.max_depth,
                })}
                {discoverCaches.data.truncated
                  ? ` ${t('The scan reached its safety limit and may be incomplete.')}` : ''}
              </p>
            </div>
          )}
        </div>

        <div className={styles.actions}>
          <Button type="submit" disabled={actionBusy || syncBusy}>
            {saveSettings.isPending ? <Spinner size={15} /> : <Save size={15} aria-hidden="true" />}
            {t('Save settings')}
          </Button>
          <Button type="button" variant="ghost" onClick={onTest}
            disabled={actionBusy || syncBusy || !baseUrl.trim() || !username.trim()
              || (!settings.password_configured && !password)}>
            {testConnection.isPending ? <Spinner size={15} /> : <Wifi size={15} aria-hidden="true" />}
            {t('Test connection')}
          </Button>
          <Button type="button" variant="ghost" onClick={onSync}
            disabled={actionBusy || syncBusy || !enabled || !cachePath.trim()
              || (!settings.password_configured && !password)}>
            {syncBusy || startSync.isPending ? <Spinner size={15} /> : <Play size={15} aria-hidden="true" />}
            {syncBusy ? t('Syncing positions…') : t('Sync now')}
          </Button>
        </div>

        <div className={styles.liveMessage} role="status" aria-live="polite">
          {message && <span className={message.ok ? styles.success : styles.failure}>{message.text}</span>}
          {error && <span className={styles.failure}>
            {error instanceof ApiError ? error.message : t('Moon+ Reader operation failed.')}
          </span>}
        </div>
      </form>

      <section className={styles.card}>
        <h2 className={styles.cardTitle}><Cloud size={17} aria-hidden="true" /> {t('Synchronization status')}</h2>
        <div className={styles.statusLine}>
          {syncBusy ? <Spinner size={18} /> : <CheckCircle2 size={18} aria-hidden="true" />}
          <div>
            <strong>{syncBusy ? t('Importing Moon+ positions…')
              : settings.sync_status === 'error' ? t('Last synchronization failed')
              : t('Ready')}</strong>
            <span>{t('Last sync: {date}', { date: displayDate(settings.last_sync_at) })}</span>
          </div>
        </div>

        {settings.last_sync_error && <p className={styles.failure}>{settings.last_sync_error}</p>}

        <div className={styles.summaryGrid}>
          {summaryEntries.map(([label, value]) => (
            <div className={styles.summaryItem} key={label}>
              <strong>{value}</strong><span>{label}</span>
            </div>
          ))}
        </div>

        {(summary?.unmatched?.length ?? 0) > 0 && (
          <details className={styles.details}>
            <summary>{t('Unmatched Moon+ files ({count})', { count: summary!.unmatched!.length })}</summary>
            <ul className={styles.fileList}>
              {summary!.unmatched!.map((name) => (
                <li key={name}>
                  <FileWarning size={15} aria-hidden="true" />
                  <span>{name}</span>
                </li>
              ))}
            </ul>
          </details>
        )}
        {(summary?.errors?.length ?? 0) > 0 && (
          <details className={styles.details}>
            <summary>{t('Import errors ({count})', { count: summary!.errors!.length })}</summary>
            <ul className={styles.fileList}>{summary!.errors!.map((item) => (
              <li key={`${item.file}:${item.message}`}>
                <FileWarning size={15} aria-hidden="true" />
                <span><strong>{item.file}</strong><small>{item.message}</small></span>
              </li>
            ))}</ul>
          </details>
        )}
      </section>

      <section className={styles.securityNote}>
        <ShieldCheck size={18} aria-hidden="true" />
        <p>{t('The WebDAV password is encrypted on the server and is never returned to the browser after saving.')}</p>
      </section>
    </main>
  );
}
