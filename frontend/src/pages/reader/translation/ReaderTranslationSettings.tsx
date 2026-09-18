import { useEffect, useMemo, useRef, useState } from 'react';
import { CheckCircle2, Pencil, Plus, RefreshCw, Trash2 } from 'lucide-react';
import {
  useCheckReaderTranslationModel,
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
} from '../../../lib/queries';
import { useT } from '../../../lib/i18n';
import styles from '../../Reader.module.css';

const PROVIDERS = {
  custom: {
    label: 'Custom OpenAI-compatible', base_url: '', endpoint_path: 'chat/completions',
    model: '', discover: false, logo: '',
  },
  opencode_zen: {
    label: 'OpenCode Zen', base_url: 'https://opencode.ai/zen/v1', endpoint_path: 'chat/completions',
    model: 'deepseek-v4-flash-free', discover: true, logo: 'opencode',
  },
  opencode_go: {
    label: 'OpenCode Go', base_url: 'https://opencode.ai/zen/go/v1', endpoint_path: 'chat/completions',
    model: 'glm-5.2', discover: true, logo: 'opencode',
  },
  nvidia_nim: {
    label: 'NVIDIA NIM', base_url: 'https://integrate.api.nvidia.com/v1', endpoint_path: 'chat/completions',
    model: 'nvidia/nemotron-3-nano-30b-a3b', discover: true, logo: 'nvidia',
  },
  openrouter: {
    label: 'OpenRouter', base_url: 'https://openrouter.ai/api/v1', endpoint_path: 'chat/completions',
    model: '', discover: true, logo: 'openrouter',
  },
  groq: {
    label: 'Groq', base_url: 'https://api.groq.com/openai/v1', endpoint_path: 'chat/completions',
    model: '', discover: true, logo: 'groq',
  },
  mistral: {
    label: 'Mistral', base_url: 'https://api.mistral.ai/v1', endpoint_path: 'chat/completions',
    model: '', discover: true, logo: 'mistral',
  },
  xai: {
    label: 'xAI', base_url: 'https://api.x.ai/v1', endpoint_path: 'chat/completions',
    model: '', discover: true, logo: 'xai',
  },
  ollama: {
    label: 'Ollama', base_url: 'http://127.0.0.1:11434/v1', endpoint_path: 'chat/completions',
    model: '', discover: true, logo: 'ollama',
  },
  lmstudio: {
    label: 'LM Studio', base_url: 'http://127.0.0.1:1234/v1', endpoint_path: 'chat/completions',
    model: '', discover: true, logo: 'lmstudio',
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

type ModelCheckStatus = 'pending' | 'checking' | 'ok' | 'error';

interface ModelCheckItem {
  model: string;
  status: ModelCheckStatus;
  latencyMs?: number;
  error?: string;
}

interface ModelCheckCache {
  version: 1;
  models: string[];
  details: ReaderTranslationModelInfo[];
  checks: ModelCheckItem[];
  lastCheckedAt: string | null;
}

const MODEL_CHECK_CACHE_PREFIX = 'cwng-reader-model-check-v1:';

function modelCheckCacheKey(profileId: string): string {
  return `${MODEL_CHECK_CACHE_PREFIX}${profileId}`;
}

function readModelCheckCache(profileId: string): ModelCheckCache | null {
  try {
    const raw = localStorage.getItem(modelCheckCacheKey(profileId));
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<ModelCheckCache>;
    if (parsed.version !== 1 || !Array.isArray(parsed.models)) return null;
    const models = parsed.models.filter((model): model is string => typeof model === 'string' && !!model);
    const modelSet = new Set(models);
    const checks = Array.isArray(parsed.checks)
      ? parsed.checks.filter((item): item is ModelCheckItem => (
        !!item && typeof item.model === 'string' && modelSet.has(item.model)
        && ['pending', 'checking', 'ok', 'error'].includes(item.status)
      )).map((item) => item.status === 'checking' ? { ...item, status: 'pending' as const } : item)
      : [];
    return {
      version: 1,
      models,
      details: Array.isArray(parsed.details) ? parsed.details : [],
      checks,
      lastCheckedAt: typeof parsed.lastCheckedAt === 'string' ? parsed.lastCheckedAt : null,
    };
  } catch {
    return null;
  }
}

function writeModelCheckCache(profileId: string, cache: ModelCheckCache): void {
  try {
    localStorage.setItem(modelCheckCacheKey(profileId), JSON.stringify(cache));
  } catch {
    // Browser storage can be disabled or full; the in-memory view still works.
  }
}

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

function providerLogoId(provider: ProviderKey): string {
  return PROVIDERS[provider].logo;
}

const MODEL_LOGO_BY_FAMILY: Record<string, string> = {
  gpt: 'openai',
  claude: 'anthropic',
  gemini: 'google',
  gemma: 'google',
  deepseek: 'deepseek',
  qwen: 'alibaba',
  llama: 'meta',
  mistral: 'mistral',
  grok: 'xai',
  kimi: 'moonshotai',
  glm: 'zhipuai',
  minimax: 'minimax',
  nemotron: 'nvidia',
  command: 'cohere',
  granite: 'ibm',
  nova: 'amazon',
  jamba: 'ai21',
  phi: 'microsoft',
  mimo: 'xiaomi',
  jina: 'jinaai',
  perplexity: 'perplexity',
};

function brandLogoUrl(id: string): string {
  // OllamaProxy uses LM Studio's official color mark because models.dev does
  // not provide an equivalent brand asset. Other providers use the shared
  // models.dev logo catalogue and fall back to initials on any load failure.
  if (id === 'lmstudio') {
    return 'https://lmstudio.ai/assets/marketing/brand/download/logos/lm-studio-icon-color.png';
  }
  return `https://models.dev/logos/${encodeURIComponent(id)}.svg`;
}


function BrandMark({ id, label, small = false }: { id: string; label: string; small?: boolean }) {
  const [failed, setFailed] = useState(false);
  const fallback = label.trim().slice(0, 2).toUpperCase() || 'AI';
  return (
    <span className={small ? styles.translationBrandSmall : styles.translationBrand}
      aria-hidden="true" title={label}>
      <span className={styles.translationBrandFallback}>{fallback}</span>
      {!!id && !failed && (
        <img src={brandLogoUrl(id)} alt="" loading="lazy"
          onError={() => setFailed(true)} />
      )}
    </span>
  );
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

interface ModelFamilyDefinition {
  key: string;
  title: string;
  patterns: RegExp[];
}

interface ModelFamilyGroup<T> {
  key: string;
  title: string;
  items: T[];
}

const MODEL_FAMILIES: ModelFamilyDefinition[] = [
  { key: 'bigpickle', title: 'Big Pickle', patterns: [/(^|[\/_.:-])big-pickle([\/_.:-]|$)/, /(^|[\/_.:-])big pickle([\/_.:-]|$)/] },
  { key: 'mimo', title: 'MiMo / Xiaomi', patterns: [/(^|[\/_.:-])mimo([\/_.:-]|$)/, /(^|[\/_.:-])xiaomi([\/_.:-]|$)/] },
  { key: 'jina', title: 'Jina AI', patterns: [/(^|[\/_.:-])jina([\/_.:-]|$)/] },
  { key: 'perplexity', title: 'Perplexity', patterns: [/(^|[\/_.:-])(perplexity|sonar)([\/_.:-]|$)/] },
  { key: 'gpt', title: 'GPT / OpenAI', patterns: [/(^|[\/_.:-])(openai|chatgpt|codex)([\/_.:-]|$)/, /(^|[\/_.:-])gpt(?:\d|[\/_.:-]|$)/, /(^|[\/_.:-])o[134]([\/_.:-]|$)/] },
  { key: 'claude', title: 'Claude', patterns: [/(^|[\/_.:-])anthropic([\/_.:-]|$)/, /(^|[\/_.:-])claude(?:\d|[\/_.:-]|$)/] },
  { key: 'gemini', title: 'Gemini', patterns: [/(^|[\/_.:-])gemini(?:\d|[\/_.:-]|$)/] },
  { key: 'gemma', title: 'Gemma', patterns: [/(^|[\/_.:-])gemma(?:\d|[\/_.:-]|$)/] },
  { key: 'deepseek', title: 'DeepSeek', patterns: [/(^|[\/_.:-])deepseek([\/_.:-]|$)/] },
  { key: 'qwen', title: 'Qwen', patterns: [/(^|[\/_.:-])qwen(?:\d|[\/_.:-]|$)/, /(^|[\/_.:-])(alibaba|aliyun)([\/_.:-]|$)/] },
  { key: 'llama', title: 'Llama', patterns: [/(^|[\/_.:-])llama(?:\d|[\/_.:-]|$)/, /(^|[\/_.:-])meta-llama([\/_.:-]|$)/] },
  { key: 'mistral', title: 'Mistral', patterns: [/(^|[\/_.:-])(mistral|mixtral|codestral|devstral|ministral|magistral|pixtral)(?:\d|[\/_.:-]|$)/] },
  { key: 'grok', title: 'Grok', patterns: [/(^|[\/_.:-])grok(?:\d|[\/_.:-]|$)/, /(^|[\/_.:-])(x-ai|xai)([\/_.:-]|$)/] },
  { key: 'kimi', title: 'Kimi / Moonshot', patterns: [/(^|[\/_.:-])(kimi|moonshot|moonshotai)([\/_.:-]|$)/] },
  { key: 'glm', title: 'GLM / Z.ai', patterns: [/(^|[\/_.:-])glm(?:\d|[\/_.:-]|$)/, /(^|[\/_.:-])(z-ai|zai|thudm)([\/_.:-]|$)/] },
  { key: 'minimax', title: 'MiniMax', patterns: [/(^|[\/_.:-])minimax([\/_.:-]|$)/] },
  { key: 'nemotron', title: 'Nemotron', patterns: [/(^|[\/_.:-])nemotron([\/_.:-]|$)/] },
  { key: 'phi', title: 'Phi / Microsoft', patterns: [/(^|[\/_.:-])phi(?:\d|[\/_.:-]|$)/] },
  { key: 'command', title: 'Command / Cohere', patterns: [/(^|[\/_.:-])(command|cohere)([\/_.:-]|$)/] },
  { key: 'granite', title: 'Granite / IBM', patterns: [/(^|[\/_.:-])granite(?:\d|[\/_.:-]|$)/, /(^|[\/_.:-])ibm([\/_.:-]|$)/] },
  { key: 'nova', title: 'Nova / Amazon', patterns: [/(^|[\/_.:-])(nova|amazon|aws)([\/_.:-]|$)/] },
  { key: 'jamba', title: 'Jamba / AI21', patterns: [/(^|[\/_.:-])(jamba|ai21)([\/_.:-]|$)/] },
  { key: 'aya', title: 'Aya', patterns: [/(^|[\/_.:-])aya([\/_.:-]|$)/] },
  { key: 'yi', title: 'Yi / 01.AI', patterns: [/(^|[\/_.:-])(yi|01-ai|zeroone)([\/_.:-]|$)/] },
  { key: 'falcon', title: 'Falcon', patterns: [/(^|[\/_.:-])falcon(?:\d|[\/_.:-]|$)/, /(^|[\/_.:-])tiiuae([\/_.:-]|$)/] },
  { key: 'dbrx', title: 'DBRX', patterns: [/(^|[\/_.:-])(dbrx|databricks)([\/_.:-]|$)/] },
  { key: 'solar', title: 'Solar / Upstage', patterns: [/(^|[\/_.:-])(solar|upstage)([\/_.:-]|$)/] },
  { key: 'internlm', title: 'InternLM', patterns: [/(^|[\/_.:-])internlm(?:\d|[\/_.:-]|$)/] },
  { key: 'seed', title: 'Seed', patterns: [/(^|[\/_.:-])seed(?:\d|[\/_.:-]|$)/, /(^|[\/_.:-])bytedance([\/_.:-]|$)/] },
  { key: 'step', title: 'Step', patterns: [/(^|[\/_.:-])(step|stepfun)([\/_.:-]|$)/] },
  { key: 'ernie', title: 'ERNIE / Baidu', patterns: [/(^|[\/_.:-])ernie(?:\d|[\/_.:-]|$)/, /(^|[\/_.:-])baidu([\/_.:-]|$)/] },
  { key: 'hunyuan', title: 'Hunyuan / Tencent', patterns: [/(^|[\/_.:-])hunyuan(?:\d|[\/_.:-]|$)/, /(^|[\/_.:-])tencent([\/_.:-]|$)/] },
  { key: 'doubao', title: 'Doubao', patterns: [/(^|[\/_.:-])doubao([\/_.:-]|$)/] },
  { key: 'baichuan', title: 'Baichuan', patterns: [/(^|[\/_.:-])baichuan(?:\d|[\/_.:-]|$)/] },
];

function modelFamily(model: string, details: ReaderTranslationModelInfo[]): ModelFamilyDefinition {
  const owner = details.find((item) => item.id === model)?.owner ?? '';
  const identity = `${owner}/${model}`.trim().toLowerCase();
  return MODEL_FAMILIES.find((family) => (
    family.patterns.some((pattern) => pattern.test(identity))
  )) ?? { key: 'other', title: 'Other models', patterns: [] };
}

function modelLogoId(
  model: string,
  details: ReaderTranslationModelInfo[],
  fallbackProvider: ProviderKey,
): string {
  const family = modelFamily(model, details);
  return MODEL_LOGO_BY_FAMILY[family.key] || providerLogoId(fallbackProvider);
}

function groupByModelFamily<T>(
  items: T[],
  modelOf: (item: T) => string,
  details: ReaderTranslationModelInfo[],
): ModelFamilyGroup<T>[] {
  const grouped = new Map<string, ModelFamilyGroup<T>>();
  for (const item of items) {
    const family = modelFamily(modelOf(item), details);
    const group = grouped.get(family.key) ?? { key: family.key, title: family.title, items: [] };
    group.items.push(item);
    grouped.set(family.key, group);
  }
  const order = new Map(MODEL_FAMILIES.map((family, index) => [family.key, index]));
  return [...grouped.values()].sort((left, right) => (
    (order.get(left.key) ?? Number.MAX_SAFE_INTEGER)
    - (order.get(right.key) ?? Number.MAX_SAFE_INTEGER)
  ));
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

function endpointForProfileModel(
  profile: ReaderTranslationProfile,
  provider: ProviderKey,
  model: string,
): string {
  if (profile.endpoint_path === 'opencode-cli') return 'opencode-cli';
  return endpointForModel(provider, model, profile.endpoint_path);
}

function profileEndpointLabel(profile: ReaderTranslationProfile): string {
  if (profile.endpoint_path === 'opencode-cli') {
    return profile.base_url + ' · OpenCode CLI';
  }
  return profile.base_url + (profile.endpoint_path ? '/' + profile.endpoint_path : '');
}

export function LlmProfileSettings({ settings, update }: {
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
  const checkModel = useCheckReaderTranslationModel();
  const profiles = profilesQuery.data?.profiles ?? [];

  const [managedProfileId, setManagedProfileId] = useState<string | null>(null);
  const [testingProfileId, setTestingProfileId] = useState<string | null>(null);
  const [profileTestResults, setProfileTestResults] = useState<Record<string, { ok: boolean; message: string }>>({});
  const [editingId, setEditingId] = useState<string | 'new' | null>(null);
  const [provider, setProvider] = useState<ProviderKey>('custom');
  const [form, setForm] = useState<ReaderTranslationProfileInput>(EMPTY_PROFILE);
  const [headersText, setHeadersText] = useState('{}');
  const [formError, setFormError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [models, setModels] = useState<string[]>([]);
  const [modelDetails, setModelDetails] = useState<ReaderTranslationModelInfo[]>([]);
  const [modelChecks, setModelChecks] = useState<ModelCheckItem[]>([]);
  const [lastModelCheckAt, setLastModelCheckAt] = useState<string | null>(null);
  const [checkingModels, setCheckingModels] = useState(false);
  const [openModelFamilies, setOpenModelFamilies] = useState<Set<string>>(() => new Set());
  const modelCheckRunRef = useRef(0);
  const autoDiscoveredProfilesRef = useRef(new Set<string>());
  const [promptDraft, setPromptDraft] = useState(settings.translationPrompt);

  const selectedProfile = useMemo(
    () => profiles.find((item) => item.id === managedProfileId)
      ?? profiles.find((item) => item.id === settings.translationProfileId)
      ?? profiles[0]
      ?? null,
    [managedProfileId, profiles, settings.translationProfileId],
  );

  const configuredProviders = useMemo(() => (
    (Object.keys(PROVIDERS) as ProviderKey[]).flatMap((key) => {
      const providerProfiles = profiles.filter((profile) => providerFor(profile.base_url) === key);
      return providerProfiles.length ? [{ key, profiles: providerProfiles }] : [];
    })
  ), [profiles]);

  const selectedProviderKey = selectedProfile ? providerFor(selectedProfile.base_url) : null;

  const selectConfiguredProvider = (key: ProviderKey) => {
    const group = configuredProviders.find((item) => item.key === key);
    if (!group?.profiles.length) return;
    const preferred = group.profiles.find((profile) => profile.id === settings.translationProfileId)
      ?? group.profiles.find((profile) => profile.id === managedProfileId)
      ?? group.profiles[0];
    setManagedProfileId(preferred.id);
  };

  useEffect(() => {
    if (!profiles.length) {
      setManagedProfileId(null);
      return;
    }
    if (!managedProfileId || !profiles.some((item) => item.id === managedProfileId)) {
      setManagedProfileId(
        profiles.some((item) => item.id === settings.translationProfileId)
          ? settings.translationProfileId
          : profiles[0].id,
      );
    }
  }, [managedProfileId, profiles, settings.translationProfileId]);

  useEffect(() => {
    setPromptDraft(settings.translationPrompt);
  }, [settings.translationPrompt]);

  useEffect(() => {
    modelCheckRunRef.current += 1;
    setCheckingModels(false);
    setOpenModelFamilies(new Set());
    if (!selectedProfile) {
      setModels([]);
      setModelDetails([]);
      setModelChecks([]);
      setLastModelCheckAt(null);
      return;
    }
    const cached = readModelCheckCache(selectedProfile.id);
    setModels(cached?.models ?? []);
    setModelDetails(cached?.details ?? []);
    setModelChecks(cached?.checks ?? []);
    setLastModelCheckAt(cached?.lastCheckedAt ?? null);
  }, [selectedProfile?.id]);

  useEffect(() => () => {
    modelCheckRunRef.current += 1;
  }, []);

  const sortedModelChecks = useMemo(() => {
    const known = new Map(modelChecks.map((item) => [item.model, item]));
    const rank: Record<ModelCheckStatus, number> = {
      checking: 0,
      ok: 1,
      pending: 2,
      error: 3,
    };
    return models.map((model) => known.get(model) ?? { model, status: 'pending' as const })
      .sort((left, right) => {
        const statusOrder = rank[left.status] - rank[right.status];
        if (statusOrder) return statusOrder;
        if (left.status === 'ok' && right.status === 'ok') {
          const latencyOrder = (left.latencyMs ?? Number.MAX_SAFE_INTEGER)
            - (right.latencyMs ?? Number.MAX_SAFE_INTEGER);
          if (latencyOrder) return latencyOrder;
        }
        return left.model.localeCompare(right.model);
      });
  }, [modelChecks, models]);

  const groupedModelChecks = useMemo(() => (
    groupByModelFamily(sortedModelChecks, (item) => item.model, modelDetails)
  ), [modelDetails, sortedModelChecks]);

  const selectedProviderLabel = selectedProfile
    ? PROVIDERS[providerFor(selectedProfile.base_url)].label
    : '';
  const selectedModelOwner = selectedProfile
    ? modelDetails.find((item) => item.id === selectedProfile.model)?.owner
    : undefined;

  useEffect(() => {
    if (!groupedModelChecks.length) return;
    setOpenModelFamilies((current) => {
      const available = new Set(groupedModelChecks.map((group) => group.key));
      const next = new Set([...current].filter((key) => available.has(key)));
      if (!next.size) {
        const selectedGroup = groupedModelChecks.find((group) => (
          group.items.some((item) => item.model === selectedProfile?.model)
        ));
        next.add(selectedGroup?.key ?? groupedModelChecks[0].key);
      }
      if (next.size === current.size && [...next].every((key) => current.has(key))) {
        return current;
      }
      return next;
    });
  }, [groupedModelChecks, selectedProfile?.model]);

  const formattedLastModelCheck = useMemo(() => {
    if (!lastModelCheckAt) return null;
    const date = new Date(lastModelCheckAt);
    if (Number.isNaN(date.getTime())) return null;
    return new Intl.DateTimeFormat(undefined, {
      dateStyle: 'medium',
      timeStyle: 'medium',
    }).format(date);
  }, [lastModelCheckAt]);

  const beginNew = () => {
    setEditingId('new');
    setProvider('custom');
    setForm({ ...EMPTY_PROFILE, extra_headers: {} });
    setHeadersText('{}');
    setFormError(null);
    setNotice(null);
  };

  const chooseProvider = (key: ProviderKey) => {
    setProvider(key);
    const preset = PROVIDERS[key];
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

  const applyModelCatalog = (
    profileId: string,
    nextModels: string[],
    nextDetails: ReaderTranslationModelInfo[],
    previousChecks: ModelCheckItem[] = modelChecks,
    checkedAt: string | null = lastModelCheckAt,
  ) => {
    const previous = new Map(previousChecks.map((item) => [item.model, item]));
    const nextChecks = nextModels.map((model) => (
      previous.get(model) ?? { model, status: 'pending' as const }
    ));
    setModels(nextModels);
    setModelDetails(nextDetails);
    setModelChecks(nextChecks);
    setLastModelCheckAt(checkedAt);
    writeModelCheckCache(profileId, {
      version: 1,
      models: nextModels,
      details: nextDetails,
      checks: nextChecks,
      lastCheckedAt: checkedAt,
    });
    return nextChecks;
  };

  useEffect(() => {
    const profile = selectedProfile;
    if (!profile) return;
    const cached = readModelCheckCache(profile.id);
    if (cached?.models.length) return;
    const providerKey = providerFor(profile.base_url);
    const preset = PROVIDERS[providerKey];
    if (!preset.discover) return;
    if ((providerKey === 'ollama' || providerKey === 'lmstudio')
        && !profilesQuery.data?.private_endpoints_allowed) return;
    if (autoDiscoveredProfilesRef.current.has(profile.id)) return;
    autoDiscoveredProfilesRef.current.add(profile.id);

    let cancelled = false;
    void modelsMutation.mutateAsync(profile.id).then((response) => {
      if (cancelled) return;
      const details = response.details ?? [];
      const checks: ModelCheckItem[] = response.models.map((model) => ({
        model, status: 'pending',
      }));
      setModels(response.models);
      setModelDetails(details);
      setModelChecks(checks);
      setLastModelCheckAt(null);
      writeModelCheckCache(profile.id, {
        version: 1,
        models: response.models,
        details,
        checks,
        lastCheckedAt: null,
      });
    }).catch(() => {
      // Auto-discovery is opportunistic. The explicit Refresh button remains
      // the error-reporting/retry path and profile editing stays usable.
    });
    return () => { cancelled = true; };
  }, [profilesQuery.data?.private_endpoints_allowed, selectedProfile?.id]);

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
        setManagedProfileId(response.profile.id);
        if (!settings.translationProfileId) update({ translationProfileId: response.profile.id });
      } else if (editingId) {
        const response = await updateProfile.mutateAsync({ id: editingId, payload });
        savedProfile = response.profile;
      } else {
        return;
      }
      setEditingId(null);
      if (PROVIDERS[provider].discover) {
        const discovered = await modelsMutation.mutateAsync(savedProfile.id);
        const details = discovered.details ?? [];
        applyModelCatalog(savedProfile.id, discovered.models, details, [], null);
        setNotice(t('{count} models loaded.', { count: discovered.models.length }));
      } else {
        try {
          localStorage.removeItem(modelCheckCacheKey(savedProfile.id));
        } catch {
          // Ignore unavailable browser storage.
        }
        if (savedProfile.id === selectedProfile?.id) {
          setModels([]);
          setModelDetails([]);
          setModelChecks([]);
          setLastModelCheckAt(null);
        }
        setNotice(t('Translation profile saved.'));
      }
    } catch (error) {
      setFormError(error instanceof Error ? error.message : t('Could not save the translation profile.'));
    }
  };

  const removeProfile = async (profile: ReaderTranslationProfile) => {
    if (!window.confirm(t('Delete this translation profile?'))) return;
    try {
      await deleteProfile.mutateAsync(profile.id);
      try {
        localStorage.removeItem(modelCheckCacheKey(profile.id));
      } catch {
        // Ignore unavailable browser storage.
      }
      const replacement = profiles.find((item) => item.id !== profile.id);
      if (selectedProfile?.id === profile.id) setManagedProfileId(replacement?.id ?? null);
      if (settings.translationProfileId === profile.id) {
        update({ translationProfileId: replacement?.id ?? '', translationEnabled: false });
      }
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

  const testListedProfile = async (profile: ReaderTranslationProfile) => {
    setTestingProfileId(profile.id);
    setProfileTestResults((current) => {
      const next = { ...current };
      delete next[profile.id];
      return next;
    });
    try {
      const response = await testProfile.mutateAsync(profile.id);
      setProfileTestResults((current) => ({
        ...current,
        [profile.id]: { ok: true, message: response.preview || t('Connection successful.') },
      }));
    } catch (error) {
      setProfileTestResults((current) => ({
        ...current,
        [profile.id]: {
          ok: false,
          message: error instanceof Error ? error.message : t('Connection test failed.'),
        },
      }));
    } finally {
      setTestingProfileId((current) => current === profile.id ? null : current);
    }
  };

  const selectCatalogModel = async (model: string) => {
    if (!selectedProfile || model === selectedProfile.model || updateProfile.isPending) return;
    setNotice(null);
    setFormError(null);
    const selectedProvider = providerFor(selectedProfile.base_url);
    try {
      await updateProfile.mutateAsync({
        id: selectedProfile.id,
        payload: {
          model,
          endpoint_path: endpointForProfileModel(
            selectedProfile, selectedProvider, model,
          ),
          max_output_tokens: maxOutputTokensForModel(
            model, selectedProfile.max_output_tokens,
          ),
        },
      });
      setNotice(t('Model {model} selected.', { model }));
    } catch (error) {
      setFormError(error instanceof Error ? error.message : t('Could not save the translation profile.'));
    }
  };

  const loadModels = async () => {
    if (!selectedProfile) return;
    setFormError(null);
    try {
      const response = await modelsMutation.mutateAsync(selectedProfile.id);
      const details = response.details ?? [];
      applyModelCatalog(selectedProfile.id, response.models, details);
      setNotice(t('{count} models loaded.', { count: response.models.length }));
    } catch (error) {
      setFormError(error instanceof Error ? error.message : t('Could not load models.'));
    }
  };

  const checkAllModels = async () => {
    if (!selectedProfile || checkingModels) return;
    const profile = selectedProfile;
    const runId = modelCheckRunRef.current + 1;
    modelCheckRunRef.current = runId;
    setCheckingModels(true);
    setFormError(null);
    setNotice(null);

    try {
      let catalogModels = models;
      let catalogDetails = modelDetails;
      if (!catalogModels.length) {
        const response = await modelsMutation.mutateAsync(profile.id);
        if (modelCheckRunRef.current !== runId) return;
        catalogModels = response.models;
        catalogDetails = response.details ?? [];
      }
      if (!catalogModels.length) {
        setFormError(t('No models were returned by the provider.'));
        return;
      }

      let checks: ModelCheckItem[] = catalogModels.map((model) => ({ model, status: 'pending' }));
      applyModelCatalog(profile.id, catalogModels, catalogDetails, checks, null);

      const providerKey = providerFor(profile.base_url);
      for (const model of catalogModels) {
        if (modelCheckRunRef.current !== runId) return;
        checks = checks.map((item) => item.model === model
          ? { model, status: 'checking' }
          : item);
        setModelChecks(checks);
        writeModelCheckCache(profile.id, {
          version: 1,
          models: catalogModels,
          details: catalogDetails,
          checks,
          lastCheckedAt: null,
        });

        try {
          const response = await checkModel.mutateAsync({
            id: profile.id,
            model,
            endpointPath: endpointForProfileModel(profile, providerKey, model),
          });
          checks = checks.map((item) => item.model === model
            ? {
              model,
              status: response.ok ? 'ok' : 'error',
              latencyMs: response.latency_ms,
              error: response.error?.message,
            }
            : item);
        } catch (error) {
          checks = checks.map((item) => item.model === model
            ? {
              model,
              status: 'error',
              error: error instanceof Error ? error.message : t('Model check failed.'),
            }
            : item);
        }
        if (modelCheckRunRef.current !== runId) return;
        setModelChecks(checks);
        writeModelCheckCache(profile.id, {
          version: 1,
          models: catalogModels,
          details: catalogDetails,
          checks,
          lastCheckedAt: null,
        });
      }

      const completedAt = new Date().toISOString();
      setLastModelCheckAt(completedAt);
      writeModelCheckCache(profile.id, {
        version: 1,
        models: catalogModels,
        details: catalogDetails,
        checks,
        lastCheckedAt: completedAt,
      });
      setNotice(t('Model check completed.'));
    } catch (error) {
      setFormError(error instanceof Error ? error.message : t('Could not load models.'));
    } finally {
      if (modelCheckRunRef.current === runId) setCheckingModels(false);
    }
  };

  return (
    <div className={styles.translationSettings}>
      <div className={styles.translationManagerHeader}>
        <div>
          <h2>{t('LLM profiles')}</h2>
          <p>{t('Configure, test, and inspect the LLM endpoints used by reader translation.')}</p>
        </div>
        <button type="button" className={styles.translationManagerAdd} onClick={beginNew}>
          <Plus size={15} /> {t('Add profile')}
        </button>
      </div>

      {profilesQuery.isLoading ? (
        <p className={styles.muted}>{t('Loading translation profiles…')}</p>
      ) : profiles.length === 0 ? (
        <div className={styles.translationProfileEmpty}>
          <p>{t('No LLM profiles yet.')}</p>
          <button type="button" onClick={beginNew}><Plus size={15} /> {t('Add profile')}</button>
        </div>
      ) : (
        <div className={styles.translationProfileList} role="list" aria-label={t('LLM profiles')}>
          {profiles.map((profile) => {
            const selected = selectedProfile?.id === profile.id;
            const active = settings.translationProfileId === profile.id;
            const providerLabel = PROVIDERS[providerFor(profile.base_url)].label;
            const testResult = profileTestResults[profile.id];
            return (
              <article key={profile.id}
                className={selected ? styles.translationProfileCardSelected : styles.translationProfileCard}
                role="listitem">
                <button type="button" className={styles.translationProfileSelect}
                  aria-pressed={selected} onClick={() => setManagedProfileId(profile.id)}>
                  <span className={styles.translationProfileIdentity}>
                    <BrandMark id={providerLogoId(providerFor(profile.base_url))} label={providerLabel} />
                    <span className={styles.translationProfileIdentityText}>
                      <span className={styles.translationProfileName}>
                        <strong>{profile.name}</strong>
                        {active && <span className={styles.translationProfileActive}>{t('Active in reader')}</span>}
                      </span>
                      <span className={styles.translationProfileMeta}>
                        {providerLabel} · {profile.model || t('No model')}
                      </span>
                    </span>
                  </span>
                  <span className={styles.translationProfileEndpoint}>
                    {profileEndpointLabel(profile)}
                  </span>
                </button>
                <div className={styles.translationProfileRowActions}>
                  <button type="button" onClick={() => {
                    setManagedProfileId(profile.id);
                    setEditingId(profile.id);
                    setProvider(providerFor(profile.base_url));
                    setForm(profileToForm(profile));
                    setHeadersText(JSON.stringify(profile.extra_headers ?? {}, null, 2));
                    setFormError(null);
                    setNotice(null);
                  }}>
                    <Pencil size={14} /> {t('Edit')}
                  </button>
                  <button type="button" onClick={() => void testListedProfile(profile)}
                    disabled={testingProfileId !== null}>
                    <CheckCircle2 size={14} />
                    {testingProfileId === profile.id ? t('Testing…') : t('Test')}
                  </button>
                  <button type="button" onClick={() => void removeProfile(profile)}
                    disabled={deleteProfile.isPending}>
                    <Trash2 size={14} /> {t('Delete')}
                  </button>
                </div>
                {testResult && (
                  <p className={testResult.ok ? styles.translationProfileTestOk : styles.translationProfileTestError}
                    role="status">
                    {testResult.ok ? '✓ ' : '× '}{testResult.message}
                  </p>
                )}
              </article>
            );
          })}
        </div>
      )}

      {editingId && (
        <div className={styles.translationProfileEditor}>
          <label>{t('Provider')}
            <span className={styles.translationProviderField}>
              <BrandMark id={providerLogoId(provider)} label={PROVIDERS[provider].label} />
              <select value={provider} onChange={(event) => chooseProvider(event.target.value as ProviderKey)}>
                {Object.entries(PROVIDERS).map(([key, value]) => (
                  <option key={key} value={key}>{value.label}</option>
                ))}
              </select>
            </span>
          </label>
          <label>{t('Name')}
            <input value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} />
          </label>
          <label>{t('Base URL')}
            <input value={form.base_url} placeholder="https://provider.example/v1"
              onChange={(event) => setForm({ ...form, base_url: event.target.value })} />
          </label>
          <label>{t('Endpoint')}
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
            <input value={form.model}
              onChange={(event) => {
                const model = event.target.value;
                setForm((current) => ({
                  ...current,
                  model,
                  max_output_tokens: maxOutputTokensForModel(model, current.max_output_tokens),
                  endpoint_path: endpointForModel(provider, model, current.endpoint_path),
                }));
              }} />
            <small className={styles.translationFieldHint}>
              {t('Enter a model ID manually or select one from the model catalog after saving the profile.')}
            </small>
          </label>
          <div className={styles.translationProfileGrid}>
            <label>{t('Temperature')}
              <input type="number" min="0" max="2" step="0.1" value={form.temperature}
                onChange={(event) => setForm({ ...form, temperature: Number(event.target.value) })} />
            </label>
            <label>{t('Max tokens')}
              <input type="number" min="64" max="32768" step="64" value={form.max_output_tokens}
                onChange={(event) => setForm({ ...form, max_output_tokens: Number(event.target.value) })} />
            </label>
            <label>{t('Timeout, s')}
              <input type="number" min="5" max="180" value={form.timeout_seconds}
                onChange={(event) => setForm({ ...form, timeout_seconds: Number(event.target.value) })} />
            </label>
          </div>
          <label className={styles.checkboxLabel}>
            <input type="checkbox" checked={form.json_mode}
              onChange={(event) => setForm({ ...form, json_mode: event.target.checked })} />
            {t('JSON')}
          </label>
          <label>{t('Headers, JSON')}
            <textarea rows={4} value={headersText} onChange={(event) => setHeadersText(event.target.value)} />
          </label>
          {(provider === 'ollama' || provider === 'lmstudio') && !profilesQuery.data?.private_endpoints_allowed && (
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

      {selectedProfile && (
        <section className={styles.translationCatalogSection} aria-labelledby="llm-model-catalog-heading">
          <div className={styles.translationCatalogHeader}>
            <div>
              <h3 id="llm-model-catalog-heading">{t('Model catalog')}</h3>
              <p>
                {t('Browse models through configured profiles. Provider rows below are catalog sources, not profiles; edit or delete profiles above.')}
              </p>
            </div>
          </div>
          <div className={styles.translationProviderModelGrid}
            aria-label={t('Models')}>
          <aside className={styles.translationProviderBrowser}>
            <div className={styles.translationBrowserHeading}>
              <strong>{t('Provider catalogs')}</strong>
              <span>{configuredProviders.length}</span>
            </div>
            <div className={styles.translationProviderList} role="list">
              {configuredProviders.map((group) => {
                const selected = selectedProviderKey === group.key;
                const preferred = group.profiles.find(
                  (profile) => profile.id === settings.translationProfileId,
                ) ?? group.profiles.find(
                  (profile) => profile.id === managedProfileId,
                ) ?? group.profiles[0];
                return (
                  <button type="button" role="listitem" key={group.key}
                    className={styles.translationProviderRow}
                    data-selected={selected ? 'true' : 'false'}
                    aria-pressed={selected}
                    onClick={() => selectConfiguredProvider(group.key)}>
                    <BrandMark id={providerLogoId(group.key)} label={PROVIDERS[group.key].label} />
                    <span>
                      <strong>{PROVIDERS[group.key].label}</strong>
                      <small>
                        {group.profiles.length > 1
                          ? group.profiles.length + ' · ' + preferred.name
                          : preferred.name}
                      </small>
                    </span>
                  </button>
                );
              })}
            </div>
          </aside>
          <div className={[styles.translationModelHealth, styles.translationModelsBrowser].join(' ')}
            aria-label={t('Models')}>
            <div className={styles.translationBrowserHeading}>
              <strong>{t('Models')}</strong>
              <span>{selectedProviderLabel}</span>
            </div>
          <div className={styles.translationModelHealthHeader}>
            <div className={styles.translationModelCatalogIdentity}>
              <BrandMark id={providerLogoId(providerFor(selectedProfile.base_url))}
                label={selectedProviderLabel} />
              <span>
                <strong>{t('Models')} · {selectedProfile.name}</strong>
                <small>
                  {selectedProviderLabel} · {selectedProfile.model}
                  {selectedModelOwner ? ` · ${selectedModelOwner}` : ''}
                </small>
              </span>
            </div>
            <span className={styles.translationModelHealthCount}>
              {t('{count} models loaded.', { count: models.length })}
            </span>
          </div>
          <div className={styles.translationModelToolbar}>
            <button type="button" onClick={() => void loadModels()}
              disabled={modelsMutation.isPending || checkingModels}>
              <RefreshCw size={15} /> {t('Refresh')}
            </button>
            <button type="button" onClick={() => void checkAllModels()}
              disabled={checkingModels || modelsMutation.isPending}>
              <RefreshCw size={15} className={checkingModels ? styles.translationModelHealthSpin : undefined} />
              {checkingModels ? t('Checking…') : t('Latency')}
            </button>
            <button type="button" onClick={() => void test()}
              disabled={testProfile.isPending || checkingModels}>
              <CheckCircle2 size={15} /> {t('Test')}
            </button>
          </div>
          <p className={styles.translationModelHealthMeta}>
            {formattedLastModelCheck
              ? t('Last check: {date}', { date: formattedLastModelCheck })
              : t('Not checked yet.')}
          </p>
          {sortedModelChecks.length > 0 ? (
            <div className={styles.translationModelHealthList} aria-live="polite">
              {groupedModelChecks.map((group) => {
                const okCount = group.items.filter((item) => item.status === 'ok').length;
                const errorCount = group.items.filter((item) => item.status === 'error').length;
                const checkingCount = group.items.filter((item) => item.status === 'checking').length;
                const label = group.key === 'other' ? t('Other models') : group.title;
                return (
                  <details className={styles.translationModelFamily} key={group.key}
                    open={openModelFamilies.has(group.key)}
                    onToggle={(event) => {
                      const isOpen = event.currentTarget.open;
                      setOpenModelFamilies((current) => {
                        const next = new Set(current);
                        if (isOpen) next.add(group.key);
                        else next.delete(group.key);
                        return next;
                      });
                    }}>
                    <summary>
                      <BrandMark small
                        id={modelLogoId(group.items[0]?.model ?? '', modelDetails, providerFor(selectedProfile.base_url))}
                        label={label} />
                      <span className={styles.translationModelFamilyName}>{label}</span>
                      <span className={styles.translationModelFamilyStats} aria-hidden="true">
                        <span>{group.items.length}</span>
                        {checkingCount > 0 && <span data-status="checking">… {checkingCount}</span>}
                        {okCount > 0 && <span data-status="ok">✓ {okCount}</span>}
                        {errorCount > 0 && <span data-status="error">× {errorCount}</span>}
                      </span>
                    </summary>
                    <div className={styles.translationModelFamilyRows} role="list">
                      {group.items.map((item) => {
                        const selected = item.model === selectedProfile.model;
                        return (
                          <button type="button" className={styles.translationModelHealthRow}
                            role="listitem" key={item.model} aria-pressed={selected}
                            data-selected={selected ? 'true' : 'false'}
                            disabled={updateProfile.isPending || checkingModels}
                            title={item.error || modelOptionLabel(item.model, modelDetails)}
                            onClick={() => void selectCatalogModel(item.model)}>
                            <BrandMark small
                              id={modelLogoId(item.model, modelDetails, providerFor(selectedProfile.base_url))}
                              label={item.model} />
                            <span className={styles.translationModelHealthName}>
                              <strong>{item.model}</strong>
                              {modelDetails.find((detail) => detail.id === item.model)?.context_length && (
                                <small>
                                  {modelDetails.find((detail) => detail.id === item.model)!.context_length!.toLocaleString()} ctx
                                </small>
                              )}
                            </span>
                            {selected && (
                              <span className={styles.translationModelSelected}>
                                <CheckCircle2 size={13} /> {t('Selected')}
                              </span>
                            )}
                            <span className={styles.translationModelHealthStatus} data-status={item.status}>
                              {item.status === 'checking'
                                ? t('Checking…')
                                : item.status === 'ok'
                                  ? `${item.latencyMs ?? 0} ms`
                                  : item.status === 'error'
                                    ? t('No response')
                                    : t('Not checked')}
                            </span>
                          </button>
                        );
                      })}
                    </div>
                  </details>
                );
              })}
            </div>
          ) : (
            <p className={styles.translationModelHealthEmpty}>
              {t('No models loaded. The check will load them first.')}
            </p>
          )}
          </div>
          </div>
        </section>
      )}

      <label>{t('Prompt')}
        <textarea rows={6} value={promptDraft}
          placeholder={t('Leave blank to use the built-in literary translation prompt.')}
          onChange={(event) => setPromptDraft(event.target.value)}
          onBlur={() => {
            if (promptDraft !== settings.translationPrompt) update({ translationPrompt: promptDraft });
          }} />
      </label>

      {formError && <p className={styles.translationError} role="alert">{formError}</p>}
      {notice && <p className={styles.translationNotice}>{notice}</p>}
    </div>
  );
}

export function ReaderTranslationSettings({ settings, update }: {
  settings: ReaderSettings;
  update: (patch: Partial<ReaderSettings>) => void;
}) {
  const t = useT();
  const profilesQuery = useReaderTranslationProfiles();
  const profiles = profilesQuery.data?.profiles ?? [];

  useEffect(() => {
    if (profiles.length && !settings.translationProfileId) {
      update({ translationProfileId: profiles[0].id });
    }
  }, [profiles, settings.translationProfileId, update]);

  return (
    <div className={styles.translationSettings}>
      <label className={styles.checkboxLabel} title={t('Automatically translate the current page')}>
        <input type="checkbox" checked={settings.translationEnabled}
          disabled={!settings.translationProfileId}
          onChange={(event) => update({
            translationEnabled: event.target.checked,
            translationView: event.target.checked ? 'translated' : 'original',
          })} />
        {t('Auto')}
      </label>
      <label className={styles.translationSwitch}
        title={t('Preserve page structure, images, and inline formatting')}>
        <span>{t('Structure')}</span>
        <input type="checkbox" role="switch"
          checked={settings.translationMode === 'structured'}
          onChange={(event) => update({
            translationMode: event.target.checked ? 'structured' : 'simple',
          })} />
        <span className={styles.translationSwitchTrack} aria-hidden="true" />
      </label>

      <label className={styles.checkboxLabel} title={t('Cache full translated pages')}>
        <input type="checkbox" checked={settings.translationCacheEnabled}
          onChange={(event) => update({ translationCacheEnabled: event.target.checked })} />
        {t('Cache')}
      </label>

      <label className={styles.checkboxLabel} title={t('Preload one translated page ahead')}>
        <input type="checkbox" checked={settings.translationPreloadNextPage}
          disabled={!settings.translationEnabled || !settings.translationCacheEnabled}
          onChange={(event) => update({ translationPreloadNextPage: event.target.checked })} />
        {t('Next page')}
      </label>
      <label>{t('From')}
        <select value={settings.translationSourceLanguage}
          onChange={(event) => update({ translationSourceLanguage: event.target.value })}>
          <option value="auto">{t('Detect automatically')}</option>
          {LANGUAGES.map(([code, label]) => <option key={code} value={code}>{label}</option>)}
        </select>
      </label>
      <label>{t('To')}
        <select value={settings.translationTargetLanguage}
          onChange={(event) => update({ translationTargetLanguage: event.target.value })}>
          {LANGUAGES.map(([code, label]) => <option key={code} value={code}>{label}</option>)}
        </select>
      </label>

      <label>{t('LLM Profile')}
        <select value={settings.translationProfileId}
          onChange={(event) => update({ translationProfileId: event.target.value })}>
          <option value="">{t('No profile selected')}</option>
          {profiles.map((profile) => (
            <option key={profile.id} value={profile.id}>{profile.name}</option>
          ))}
        </select>
      </label>
      {profilesQuery.isLoading && <p className={styles.muted}>{t('Loading translation profiles…')}</p>}
      {!profilesQuery.isLoading && profiles.length === 0 && (
        <p className={styles.translationWarning}>
          {t('No LLM profiles are configured. Add one in LLM Settings.')}
        </p>
      )}
    </div>
  );
}
