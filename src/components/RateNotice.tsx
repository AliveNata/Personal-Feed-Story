import React from 'react';
import { RateInfo } from '../types';

interface RateNoticeProps {
  rate: RateInfo | null;
  platformLabel: string;
}

const minutes = (seconds: number) => Math.max(1, Math.ceil(seconds / 60));

/** Warns before the platform starts refusing requests, based on the backend's request budget. */
const RateNotice: React.FC<RateNoticeProps> = ({ rate, platformLabel }) => {
  if (!rate || !rate.nearLimit) return null;

  const message =
    rate.waitSeconds > 0
      ? `Batas request ${platformLabel} tercapai. Tunggu sekitar ${minutes(rate.waitSeconds)} menit sebelum scrape atau download lagi.`
      : `Sudah ${rate.used} dari ${rate.limit} request ${platformLabel} dalam ${minutes(rate.windowSeconds)} menit terakhir. Kasih jeda beberapa menit biar akun nggak kena limit.`;

  return (
    <p role="status" className="mb-4 rounded-lg border border-amber-400 bg-amber-50 p-4 text-sm text-amber-900">
      {message}
    </p>
  );
};

export default RateNotice;
