import React, { useCallback, useEffect, useState } from 'react';
import { AiQuota, AiQuotaEntry, AiQuotaLimit, ClaudeSpend } from '../types';
import { ApiError, fetchAiQuota, formatRelativeTime } from '../utils/api';
import { secondaryButton } from './ui';

const RESOURCE_LABELS: Record<string, string> = {
  requests: 'Request',
  tokens: 'Token',
  'input-tokens': 'Token input',
  'output-tokens': 'Token output',
  'audio-seconds': 'Detik audio',
};

const SCOPE_LABELS: Record<AiQuotaEntry['scope'], string> = {
  transcribe: 'transkrip',
  translate: 'terjemahan',
};

const PROVIDER_LABELS: Record<AiQuotaEntry['provider'], string> = {
  groq: 'Groq',
  anthropic: 'Anthropic',
};

const number = new Intl.NumberFormat('id-ID');

const formatCount = (value: string | null): string => {
  if (value === null) return '?';
  const parsed = Number(value);
  return Number.isFinite(parsed) ? number.format(parsed) : value;
};

// Groq sends a wait like "6m14s", Anthropic a moment in time.
const formatReset = (reset: string | null): string | null => {
  if (!reset) return null;
  const at = Date.parse(reset);
  if (Number.isNaN(at)) return `reset dalam ${reset}`;
  return `reset ${new Date(at).toLocaleString('id-ID', { dateStyle: 'short', timeStyle: 'short' })}`;
};

const LimitRow: React.FC<{ limit: AiQuotaLimit }> = ({ limit }) => {
  const total = Number(limit.limit);
  const left = Number(limit.remaining);
  const share = Number.isFinite(total) && total > 0 && Number.isFinite(left) ? left / total : null;
  const reset = formatReset(limit.reset);
  const label = RESOURCE_LABELS[limit.resource] ?? limit.resource;
  return (
    <li>
      <div className="flex items-baseline justify-between gap-3 text-sm">
        <span className="text-gray-700">{label}</span>
        <span className="font-medium tabular-nums text-gray-900">
          sisa {formatCount(limit.remaining)}
          {limit.limit !== null && <span className="font-normal text-gray-500"> / {formatCount(limit.limit)}</span>}
        </span>
      </div>
      {share !== null && (
        <div
          role="meter"
          aria-label={`Sisa ${label}`}
          aria-valuemin={0}
          aria-valuemax={total}
          aria-valuenow={left}
          className="mt-1 h-1.5 rounded-full bg-gray-200"
        >
          <div
            className={`h-full rounded-full ${share < 0.1 ? 'bg-red-600' : share < 0.3 ? 'bg-amber-500' : 'bg-green-600'}`}
            style={{ width: `${Math.min(Math.max(share, 0), 1) * 100}%` }}
          />
        </div>
      )}
      {reset && <p className="mt-0.5 text-xs text-gray-500">{reset}</p>}
    </li>
  );
};

const usd = (value: number): string =>
  `$${value < 0.01 && value > 0 ? value.toFixed(4) : value.toFixed(2)}`;

const SpendSection: React.FC<{ spend: ClaudeSpend }> = ({ spend }) => {
  const share = spend.budgetUsd ? spend.usd / spend.budgetUsd : null;
  const monthName = new Date(`${spend.month}-01T00:00:00`).toLocaleDateString('id-ID', {
    month: 'long',
    year: 'numeric',
  });
  return (
    <div>
      <h3 className="text-sm font-semibold text-gray-900">Claude, pemakaian {monthName}</h3>
      <p className="mb-2 text-xs text-gray-500">
        Perkiraan dari panggilan app ini aja, angka pasti ada di console.
      </p>
      <div className="flex items-baseline justify-between gap-3 text-sm">
        <span className="text-gray-700">Biaya</span>
        <span className="font-medium tabular-nums text-gray-900">
          ≈ {usd(spend.usd)}
          {spend.budgetUsd !== null && (
            <span className="font-normal text-gray-500"> dari budget {usd(spend.budgetUsd)}</span>
          )}
        </span>
      </div>
      {share !== null && (
        <div
          role="meter"
          aria-label="Budget Claude terpakai"
          aria-valuemin={0}
          aria-valuemax={spend.budgetUsd ?? 0}
          aria-valuenow={spend.usd}
          className="mt-1 h-1.5 rounded-full bg-gray-200"
        >
          <div
            className={`h-full rounded-full ${share >= 0.9 ? 'bg-red-600' : share >= 0.7 ? 'bg-amber-500' : 'bg-green-600'}`}
            style={{ width: `${Math.min(share, 1) * 100}%` }}
          />
        </div>
      )}
      <p className="mt-1 text-xs text-gray-500">
        Token: {number.format(spend.inputTokens)} masuk, {number.format(spend.outputTokens)} keluar
        {spend.budgetUsd === null && '. Belum ada budget, set CLAUDE_MONTHLY_BUDGET_USD di backend/.env'}
      </p>
      {share !== null && share >= 1 && (
        <p role="alert" className="mt-1 text-xs font-medium text-red-700">
          Budget habis: "Bikin lebih santai (Claude)" dimatiin sampai bulan depan.
        </p>
      )}
    </div>
  );
};

type State =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; quota: AiQuota };

/**
 * Remaining Groq and Anthropic limits as last reported by their own answers. Neither has a
 * call that only reports limits, so the numbers are as old as the latest subtitle job.
 */
const AiQuotaCard: React.FC = () => {
  const [state, setState] = useState<State>({ status: 'loading' });

  const load = useCallback(() => {
    fetchAiQuota()
      .then((quota) => setState({ status: 'ready', quota }))
      .catch((caught: unknown) => {
        console.error('fetchAiQuota failed', caught);
        setState({
          status: 'error',
          message: caught instanceof ApiError ? caught.message : 'Gagal memuat kuota AI',
        });
      });
  }, []);

  useEffect(load, [load]);

  return (
    <section aria-labelledby="ai-quota-title" className="rounded-lg bg-white p-5 shadow-md">
      <div className="flex items-start justify-between gap-3">
        <h2 id="ai-quota-title" className="text-xl font-bold text-gray-900">
          Kuota AI (subtitle)
        </h2>
        <button type="button" className={secondaryButton} onClick={load}>
          Muat ulang
        </button>
      </div>
      <p className="mt-1 text-sm text-gray-600">
        Angka dari jawaban Groq dan Anthropic yang terakhir, jadi update tiap ada transkrip atau
        terjemahan.
      </p>

      {state.status === 'loading' && <p className="mt-4 text-sm text-gray-600">Memuat…</p>}
      {state.status === 'error' && (
        <p role="alert" className="mt-4 text-sm text-red-700">
          {state.message}
        </p>
      )}
      {state.status === 'ready' && (
        <div className="mt-4 space-y-5">
          {!state.quota.groqConfigured && (
            <p className="text-sm text-amber-800">GROQ_API_KEY belum diisi di backend/.env.</p>
          )}
          {state.quota.entries.length === 0 ? (
            <p className="text-sm text-gray-600">
              Belum ada data. Muncul setelah generate subtitle pertama.
            </p>
          ) : (
            state.quota.entries.map((entry) => (
              <div key={`${entry.provider}-${entry.scope}`}>
                <h3 className="text-sm font-semibold text-gray-900">
                  {PROVIDER_LABELS[entry.provider]}, {SCOPE_LABELS[entry.scope]}
                </h3>
                <p className="mb-2 text-xs text-gray-500">
                  {entry.model}, dicek {formatRelativeTime(entry.updatedAt)}
                </p>
                <ul className="space-y-2">
                  {entry.limits.map((limit) => (
                    <LimitRow key={limit.resource} limit={limit} />
                  ))}
                </ul>
              </div>
            ))
          )}
          {state.quota.anthropicConfigured && <SpendSection spend={state.quota.claudeSpend} />}
          <p className="border-t border-gray-100 pt-3 text-xs text-gray-600">
            Saldo kredit Anthropic nggak bisa dibaca lewat API, cek di{' '}
            <a
              href="https://console.anthropic.com/settings/billing"
              target="_blank"
              rel="noreferrer"
              className="font-medium text-blue-700 underline"
            >
              halaman billing console
            </a>
            . Groq free tier nggak punya saldo, cuma limit di atas.
          </p>
        </div>
      )}
    </section>
  );
};

export default AiQuotaCard;
