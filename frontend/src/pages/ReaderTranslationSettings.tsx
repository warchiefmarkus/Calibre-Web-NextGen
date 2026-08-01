import { useEffect, useMemo, useState } from 'react';
import { CheckCircle2, Pencil, Plus, RefreshCw, Trash2 } from 'lucide-react';
import {
  useCreateReaderTranslationProfile,
  useDeleteReaderTranslationProfile,
  useReaderTranslationModels,
  useReaderTranslationProfiles,
  useTestReaderTranslationProfile,
  useUpdateReaderTranslationProfile,
  type ReaderSettings,
  type ReaderTranslationModelInfo,
  type ReaderTranslationProfile,
  type ReaderTranslationProfileInput,
} from '../lib/queries';
import { useT } from '../lib/i18n';
import styles from './Reader.module.css';

const PROVIDERS = {
  custom: {
    label: 'Custom OpenAI-compatible', base_url: '', endpoint_path: 'chat/completions',
    model: '', discover: false,
  },
  opencode_zen: {
    label: 'OpenCode Zen', base_url: 'https://opencode.ai/zen/v1', endpoint_path: 'chat/completions',
    model: 'deepseek-v4-flash-free', discover: true,
  },
  opencode_go: {
    label: 'OpenCode Go', base_url: 'https://opencode.ai/zen/go/v1', endpoint_path: 'chat/completions',
    model: 'glm-5.2', discover: true,
  },
  nvidia_nim: {
    label: 'NVIDIA NIM', base_url: 'https://integrate.api.nvidia.com/v1', endpoint_path: 'chat/completions',
    model: 'nvidia/nemotron-3-nano-30b-a3b', discover: true,
  },
  openrouter: {
    label: 'OpenRouter', base_url: 'https://openrouter.ai/api/v1', endpoint_path: 'chat/completions',
    model: '', discover: false,
  },
  groq: {
    label: 'Groq', base_url: 'https://api.groq.com/openai/v1', endpoint_path: 'chat/completions',
    model: '', discover: false,
  },
  mistral: {
    label: 'Mistral', base_url: 'https://api.mistral.ai/v1', endpoint_path: 'chat/completions',
    model: '', discover: false,
  },
  ollama: {
    label: 'Ollama', base_url: 'http://127.0.0.1:11434/v1', endpoint_path: 'chat/completions',
    model: '', discover: false,
  },
} as const;

type ProviderKey = keyof typeof PROVIDERS;

const LANGUAGES = [
  ['uk', 'Українська'], ['en', 'English'], ['ru', 'Русский'], ['pl', 'Polski'],
  ['de', 'Deutsch'], ['fr', 'Français'], ['es', 'Español'], ['it', 'Italiano'],
  ['pt', 'Português'], ['cs', 'Čeština'], ['ja', '日本語'], ['zh', '中文'],
] as const;

const EMPTY_PROFILE: ReaderTranslationProfileInput = {
  name: '',
  base_url: '',
  endpoint_path: 'chat/completions',
  api_key: '',
  model: '',
  temperature: 0.2,
  max_output_tokens: 4096,
  timeout_seconds: 60,
  json_mode: true,
  extra_headers: {},
};

function profileToForm(profile: ReaderTranslationProfile): ReaderTranslationProfileInput {
  return {
    name: profile.name,
    base_url: profile.base_url,
    endpoint_path: profile.endpoint_path,
    api_key: '',
    model: profile.model,
    temperature: profile.temperature,
    max_output_tokens: profile.max_output_tokens,
    timeout_seconds: profile.timeout_seconds,
    json_mode: profile.json_mode,
    extra_headers: profile.extra_headers,
  };
}

function providerFor(baseUrl: string): ProviderKey {
  const found = Object.entries(PROVIDERS).find(([, value]) => value.base_url === baseUrl);
  return (found?.[0] as ProviderKey | undefined) ?? 'custom';
}

function maxOutputTokensForModel(model: string, configured: number): number {
  return model.trim().toLowerCase() === 'big-pickle' ? Math.max(configured, 8192) : configured;
}

function modelOptionLabel(model: string, details: ReaderTranslationModelInfo[]): string {
  const detail = details.find((item) => item.id === model);
  if (!detail) return model;
  const suffix = [
    detail.owner,
    detail.context_length ? `${detail.context_length.toLocaleString()} ctx` : '',
  ].filter(Boolean).join(' · ');
  return suffix ? `${model} — ${suffix}` : model;
}

function endpointForModel(provider: ProviderKey, model: string, fallback: string): string {
  // OpenCode's /models feeds currently expose model IDs but not the protocol
  // endpoint. Keep the documented family mapping here so discovery can still
  // configure mixed Responses, Messages, Gemini, and Chat Completions models.
  const id = model.trim().toLowerCase();
  if (!id) return fallback;
  if (provider === 'opencode_zen') {
    if (id.startsWith('gpt-')) return 'responses';
    if (id.startsWith('claude-') || id.startsWith('qwen')) return 'messages';
    if (id.startsWith('gemini-')) return `models/${model.trim()}:generateContent`;
    return 'chat/completions';
  }
  if (provider === 'opencode_go') {
    if (id.startsWith('gpt-')) return 'responses';
    if (id.startsWith('minimax-') || id.startsWith('qwen')) return 'messages';
    return 'chat/completions';
  }
  return fallback;
}

export function ReaderTranslationSettings({ settings, update }: {
  settings: ReaderSettings;
  update: (patch: Partial<ReaderSettings>) => void;
}) {
  const t = useT();
  const profilesQuery = useReaderTranslationProfiles();
  const createProfile = useCreateReaderTranslationProfile();
  const updateProfile = useUpdateReaderTranslationProfile();
  const deleteProfile = useDeleteReaderTranslationProfile();
  const testProfile = useTestReaderTranslationProfile();
  const modelsMutation = useReaderTranslationModels();
  const profiles = profilesQuery.data?.profiles ?? [];

  const [editingId, setEditingId] = useState<string | 'new' | null>(null);
  const [provider, setProvider] = useState<ProviderKey>('custom');
  const [form, setForm] = useState<ReaderTranslationProfileInput>(EMPTY_PROFILE);
  const [headersText, setHeadersText] = useState('{}');
  const [formError, setFormError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [models, setModels] = useState<string[]>([]);
  const [modelDetails, setModelDetails] = useState<ReaderTranslationModelInfo[]>([]);
  const [promptDraft, setPromptDraft] = useState(settings.translationPrompt);

  const selectedProfile = useMemo(
    () => profiles.find((item) => item.id === settings.translationProfileId) ?? null,
    [profiles, settings.translationProfileId],
  );

  useEffect(() => {
    if (profiles.length && !settings.translationProfileId) {
      update({ translationProfileId: profiles[0].id });
    }
  }, [profiles, settings.translationProfileId, update]);

  useEffect(() => {
    setPromptDraft(settings.translationPrompt);
  }, [settings.translationPrompt]);

  const beginNew = () => {
    setEditingId('new');
    setProvider('custom');
    setForm({ ...EMPTY_PROFILE, extra_headers: {} });
    setHeadersText('{}');
    setFormError(null);
    setNotice(null);
    setModels([]);
    setModelDetails([]);
  };

  const beginEdit = () => {
    if (!selectedProfile) return;
    setEditingId(selectedProfile.id);
    setProvider(providerFor(selectedProfile.base_url));
    setForm(profileToForm(selectedProfile));
    setHeadersText(JSON.stringify(selectedProfile.extra_headers ?? {}, null, 2));
    setFormError(null);
    setNotice(null);
    setModels([]);
    setModelDetails([]);
  };

  const chooseProvider = (key: ProviderKey) => {
    setProvider(key);
    const preset = PROVIDERS[key];
    setModels([]);
    setModelDetails([]);
    setForm((current) => {
      const model = preset.model || current.model;
      return {
        ...current,
        base_url: preset.base_url || current.base_url,
        endpoint_path: endpointForModel(key, model, preset.endpoint_path),
        model,
        max_output_tokens: maxOutputTokensForModel(model, current.max_output_tokens),
        name: current.name || (key === 'custom' ? '' : preset.label),
      };
    });
  };

  const save = async () => {
    setFormError(null);
    setNotice(null);
    let extraHeaders: Record<string, string>;
    try {
      const parsed = JSON.parse(headersText || '{}') as unknown;
      if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') throw new Error();
      extraHeaders = Object.fromEntries(Object.entries(parsed).map(([key, value]) => [key, String(value)]));
    } catch {
      setFormError(t('Additional headers must be a valid JSON object.'));
      return;
    }
    const payload = { ...form, extra_headers: extraHeaders };
    try {
      let savedProfile: ReaderTranslationProfile;
      if (editingId === 'new') {
        const response = await createProfile.mutateAsync(payload);
        savedProfile = response.profile;
        update({ translationProfileId: response.profile.id });
      } else if (editingId) {
        const response = await updateProfile.mutateAsync({ id: editingId, payload });
        savedProfile = response.profile;
      } else {
        return;
      }
      setEditingId(null);
      if (PROVIDERS[provider].discover) {
        const discovered = await modelsMutation.mutateAsync(savedProfile.id);
        setModels(discovered.models);
        setModelDetails(discovered.details ?? []);
        setNotice(t('{count} models loaded.', { count: discovered.models.length }));
      } else {
        setNotice(t('Translation profile saved.'));
      }
    } catch (error) {
      setFormError(error instanceof Error ? error.message : t('Could not save the translation profile.'));
    }
  };

  const remove = async () => {
    if (!selectedProfile || !window.confirm(t('Delete this translation profile?'))) return;
    try {
      await deleteProfile.mutateAsync(selectedProfile.id);
      const replacement = profiles.find((item) => item.id !== selectedProfile.id);
      update({ translationProfileId: replacement?.id ?? '', translationEnabled: false });
      setNotice(t('Translation profile deleted.'));
    } catch (error) {
      setFormError(error instanceof Error ? error.message : t('Could not delete the translation profile.'));
    }
  };

  const test = async () => {
    if (!selectedProfile) return;
    setNotice(null);
    setFormError(null);
    try {
      const response = await testProfile.mutateAsync(selectedProfile.id);
      setNotice(`${t('Connection successful.')} ${response.preview}`);
    } catch (error) {
      setFormError(error instanceof Error ? error.message : t('Connection test failed.'));
    }
  };

  const loadModels = async () => {
    if (!selectedProfile) return;
    setFormError(null);
    try {
      const response = await modelsMutation.mutateAsync(selectedProfile.id);
      setModels(response.models);
      setModelDetails(response.details ?? []);
      setNotice(t('{count} models loaded.', { count: response.models.length }));
    } catch (error) {
      setFormError(error instanceof Error ? error.message : t('Could not load models.'));
    }
  };

  return (
    <div className={styles.translationSettings}>
      <label className={styles.checkboxLabel}>
        <input type="checkbox" checked={settings.translationEnabled}
          disabled={!settings.translationProfileId}
          onChange={(event) => update({
            translationEnabled: event.target.checked,
            translationView: event.target.checked ? 'translated' : 'original',
          })} />
        {t('Automatically translate the current page')}
      </label>

      <label className={styles.checkboxLabel}>
        <input type="checkbox" checked={settings.translationCacheEnabled}
          onChange={(event) => update({ translationCacheEnabled: event.target.checked })} />
        {t('Cache full translated pages')}
      </label>

      <label className={styles.checkboxLabel}>
        <input type="checkbox" checked={settings.translationPreloadNextPage}
          disabled={!settings.translationEnabled || !settings.translationCacheEnabled}
          onChange={(event) => update({ translationPreloadNextPage: event.target.checked })} />
        {t('Preload one translated page ahead')}
      </label>

      <label>{t('Source language')}
        <select value={settings.translationSourceLanguage}
          onChange={(event) => update({ translationSourceLanguage: event.target.value })}>
          <option value="auto">{t('Detect automatically')}</option>
          {LANGUAGES.map(([code, label]) => <option key={code} value={code}>{label}</option>)}
        </select>
      </label>
      <label>{t('Target language')}
        <select value={settings.translationTargetLanguage}
          onChange={(event) => update({ translationTargetLanguage: event.target.value })}>
          {LANGUAGES.map(([code, label]) => <option key={code} value={code}>{label}</option>)}
        </select>
      </label>

      <label>{t('LLM profile')}
        <select value={settings.translationProfileId}
          onChange={(event) => update({ translationProfileId: event.target.value })}>
          <option value="">{t('No profile selected')}</option>
          {profiles.map((profile) => (
            <option key={profile.id} value={profile.id}>{profile.name} · {profile.model}</option>
          ))}
        </select>
      </label>

      <div className={styles.translationProfileActions}>
        <button type="button" onClick={beginNew}><Plus size={15} /> {t('Add')}</button>
        <button type="button" onClick={beginEdit} disabled={!selectedProfile}>
          <Pencil size={15} /> {t('Edit')}
        </button>
        <button type="button" onClick={() => void remove()} disabled={!selectedProfile}>
          <Trash2 size={15} /> {t('Delete')}
        </button>
      </div>

      {selectedProfile && !editingId && (
        <div className={styles.translationProfileSummary}>
          <strong>{selectedProfile.name}</strong>
          <span>{selectedProfile.base_url}/{selectedProfile.endpoint_path}</span>
          <span>{selectedProfile.model} · {selectedProfile.has_api_key ? t('API key saved') : t('No API key')}</span>
          <div className={styles.translationProfileActions}>
            <button type="button" onClick={() => void test()} disabled={testProfile.isPending}>
              <CheckCircle2 size={15} /> {t('Test connection')}
            </button>
            <button type="button" onClick={() => void loadModels()} disabled={modelsMutation.isPending}>
              <RefreshCw size={15} /> {t('Load models')}
            </button>
          </div>
          {models.length > 0 && (
            <select aria-label={t('Available models')} value=""
              onChange={(event) => {
                const model = event.target.value;
                if (!model) return;
                const selectedProvider = providerFor(selectedProfile.base_url);
                setEditingId(selectedProfile.id);
                setProvider(selectedProvider);
                setForm({
                  ...profileToForm(selectedProfile),
                  model,
                  max_output_tokens: maxOutputTokensForModel(
                    model, selectedProfile.max_output_tokens,
                  ),
                  endpoint_path: endpointForModel(
                    selectedProvider, model, selectedProfile.endpoint_path,
                  ),
                });
                setHeadersText(JSON.stringify(selectedProfile.extra_headers ?? {}, null, 2));
              }}>
              <option value="">{t('Choose a model to edit the profile')}</option>
              {models.map((model) => (
                <option key={model} value={model}>{modelOptionLabel(model, modelDetails)}</option>
              ))}
            </select>
          )}
        </div>
      )}

      {editingId && (
        <div className={styles.translationProfileEditor}>
          <label>{t('Provider preset')}
            <select value={provider} onChange={(event) => chooseProvider(event.target.value as ProviderKey)}>
              {Object.entries(PROVIDERS).map(([key, value]) => (
                <option key={key} value={key}>{value.label}</option>
              ))}
            </select>
          </label>
          <label>{t('Profile name')}
            <input value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} />
          </label>
          <label>{t('Base URL')}
            <input value={form.base_url} placeholder="https://provider.example/v1"
              onChange={(event) => setForm({ ...form, base_url: event.target.value })} />
          </label>
          <label>{t('Endpoint path')}
            <input value={form.endpoint_path}
              onChange={(event) => setForm({ ...form, endpoint_path: event.target.value })} />
          </label>
          <label>{t('API key')}
            <input type="password" value={form.api_key ?? ''}
              placeholder={editingId === 'new' ? '' : t('Leave blank to keep the saved key')}
              autoComplete="new-password"
              onChange={(event) => setForm({ ...form, api_key: event.target.value })} />
          </label>
          <label>{t('Model')}
            <input value={form.model} list="reader-translation-models"
              onChange={(event) => {
                const model = event.target.value;
                setForm((current) => ({
                  ...current,
                  model,
                  max_output_tokens: maxOutputTokensForModel(model, current.max_output_tokens),
                  endpoint_path: endpointForModel(provider, model, current.endpoint_path),
                }));
              }} />
            <datalist id="reader-translation-models">
              {models.map((model) => (
                <option key={model} value={model}>{modelOptionLabel(model, modelDetails)}</option>
              ))}
            </datalist>
          </label>
          <div className={styles.translationProfileGrid}>
            <label>{t('Temperature')}
              <input type="number" min="0" max="2" step="0.1" value={form.temperature}
                onChange={(event) => setForm({ ...form, temperature: Number(event.target.value) })} />
            </label>
            <label>{t('Max output tokens')}
              <input type="number" min="64" max="32768" step="64" value={form.max_output_tokens}
                onChange={(event) => setForm({ ...form, max_output_tokens: Number(event.target.value) })} />
            </label>
            <label>{t('Timeout (seconds)')}
              <input type="number" min="5" max="180" value={form.timeout_seconds}
                onChange={(event) => setForm({ ...form, timeout_seconds: Number(event.target.value) })} />
            </label>
          </div>
          <label className={styles.checkboxLabel}>
            <input type="checkbox" checked={form.json_mode}
              onChange={(event) => setForm({ ...form, json_mode: event.target.checked })} />
            {t('Request JSON output when supported')}
          </label>
          <label>{t('Additional HTTP headers (JSON)')}
            <textarea rows={4} value={headersText} onChange={(event) => setHeadersText(event.target.value)} />
          </label>
          {provider === 'ollama' && !profilesQuery.data?.private_endpoints_allowed && (
            <p className={styles.translationWarning}>
              {t('Private endpoints are blocked. Set CWNG_READER_TRANSLATION_ALLOW_PRIVATE_ENDPOINTS=true on the server to use Ollama or another LAN model.')}
            </p>
          )}
          <div className={styles.translationProfileActions}>
            <button type="button" onClick={() => void save()}
              disabled={createProfile.isPending || updateProfile.isPending}>{t('Save')}</button>
            <button type="button" onClick={() => setEditingId(null)}>{t('Cancel')}</button>
          </div>
        </div>
      )}

      <label>{t('Translation prompt')}
        <textarea rows={6} value={promptDraft}
          placeholder={t('Leave blank to use the built-in literary translation prompt.')}
          onChange={(event) => setPromptDraft(event.target.value)}
          onBlur={() => {
            if (promptDraft !== settings.translationPrompt) update({ translationPrompt: promptDraft });
          }} />
      </label>

      {profilesQuery.isLoading && <p className={styles.muted}>{t('Loading translation profiles…')}</p>}
      {formError && <p className={styles.translationError} role="alert">{formError}</p>}
      {notice && <p className={styles.translationNotice}>{notice}</p>}
    </div>
  );
}
