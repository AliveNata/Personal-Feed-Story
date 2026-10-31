import React, { useState } from "react";
import { Instagram } from "lucide-react";
import { useAuth } from "../auth/AuthContext";
import TikTokIcon from "../components/TikTokIcon";
import { focusRing, secondaryButton } from "../components/ui";
import { Favorite, FavoriteUpdate, Platform } from "../types";
import { ApiError, formatRelativeTime } from "../utils/api";
import { useFavorites } from "./FavoritesContext";

interface FavoritesBarProps {
  activePlatform: Platform;
  // Platform picked in the search form. Only favorites of that platform are shown.
  shownPlatform: Platform;
  activeUsername: string;
  onOpen: (platform: Platform, username: string) => void;
}

const PLATFORM_LABELS: Record<Platform, string> = {
  instagram: "Instagram",
  tiktok: "TikTok",
};
// "3 post baru", or just "post baru" for an update recorded before counts existed.
const updateNotes = (favorite: Favorite): string[] =>
  favorite.updates.map((kind: FavoriteUpdate) =>
    kind === "post" && favorite.newPostCount > 0
      ? `${favorite.newPostCount} post baru`
      : `${kind} baru`,
  );

const describe = (favorite: Favorite): string => {
  const parts = [
    `@${favorite.username}, ${PLATFORM_LABELS[favorite.platform]}`,
  ];
  const notes = updateNotes(favorite);
  if (notes.length) parts.push(`ada ${notes.join(" dan ")}`);
  if (favorite.checkError) parts.push(`gagal dicek: ${favorite.checkError}`);
  return parts.join(", ");
};

const Avatar: React.FC<{ favorite: Favorite }> = ({ favorite }) => {
  const [failed, setFailed] = useState(false);
  // Signed CDN links expire between checks, the initial stands in until the next check.
  if (!favorite.avatarUrl || failed) {
    return (
      <span className="flex h-full w-full items-center justify-center bg-gray-200 text-lg font-bold text-gray-800">
        {favorite.username.charAt(0).toUpperCase()}
      </span>
    );
  }
  return (
    <img
      src={favorite.avatarUrl}
      alt=""
      loading="lazy"
      onError={() => setFailed(true)}
      className="h-full w-full object-cover"
    />
  );
};

const lastChecked = (favorites: Favorite[]): string | null => {
  const times = favorites
    .map((favorite) => favorite.checkedAt)
    .filter((value): value is string => !!value);
  if (!times.length) return null;
  return times.reduce((newest, value) => (value > newest ? value : newest));
};

// "menit ini" from Intl reads oddly for something that just happened.
const describeCheckedAt = (isoDate: string): string =>
  Date.now() - new Date(isoDate).getTime() < 60_000
    ? "barusan"
    : formatRelativeTime(isoDate);

const FavoritesBar: React.FC<FavoritesBarProps> = ({
  activePlatform,
  shownPlatform,
  activeUsername,
  onOpen,
}) => {
  const { user } = useAuth();
  const { status, favorites, errorMessage, isChecking, reload, checkNow } =
    useFavorites();
  const [checkError, setCheckError] = useState<string | null>(null);

  // Favorites are personal, like the Library: only shown after login.
  if (!user) return null;

  const handleCheck = async () => {
    setCheckError(null);
    try {
      await checkNow();
    } catch (error) {
      console.error("checkFavoritesNow failed", error);
      setCheckError(
        error instanceof ApiError ? error.message : "Gagal memulai pengecekan",
      );
    }
  };

  const visible = favorites.filter(
    (favorite) => favorite.platform === shownPlatform,
  );
  const checkedAt = lastChecked(visible);

  return (
    <section
      aria-labelledby="favorites-title"
      className="mb-6 rounded-lg bg-white p-4 shadow-md"
    >
      <div className="mb-3 flex flex-wrap items-center gap-x-3 gap-y-2">
        <h2 id="favorites-title" className="text-base font-bold text-gray-900">
          Favorit
        </h2>
        {status === "ready" && visible.length > 0 && (
          <>
            <p
              role="status"
              aria-live="polite"
              className="text-sm text-gray-700"
            >
              {isChecking
                ? "Mengecek update..."
                : checkedAt
                  ? `Dicek ${describeCheckedAt(checkedAt)}`
                  : "Belum pernah dicek"}
            </p>
            <button
              type="button"
              onClick={() => void handleCheck()}
              disabled={isChecking}
              className={`ml-auto ${secondaryButton}`}
            >
              {isChecking ? "Mengecek..." : "Cek sekarang"}
            </button>
          </>
        )}
      </div>
      {checkError && (
        <p role="alert" className="mb-2 text-sm text-red-700">
          {checkError}
        </p>
      )}

      {status === "loading" && (
        <p role="status" className="text-sm text-gray-700">
          Memuat favorit...
        </p>
      )}
      {status === "error" && (
        <div role="alert" className="flex flex-wrap items-center gap-3">
          <p className="text-sm text-red-700">{errorMessage}</p>
          <button type="button" onClick={reload} className={secondaryButton}>
            Coba lagi
          </button>
        </div>
      )}
      {status === "ready" && visible.length === 0 && (
        <p className="text-sm text-gray-700">
          Belum ada favorit {PLATFORM_LABELS[shownPlatform]}. Buka feed sebuah
          akun, lalu klik "Favoritkan".
        </p>
      )}

      {status === "ready" && visible.length > 0 && (
        <ul className="flex gap-3 overflow-x-auto p-1">
          {visible.map((favorite) => {
            const hasUpdate = favorite.updates.length > 0;
            const isActive =
              favorite.platform === activePlatform &&
              favorite.username.toLowerCase() === activeUsername.toLowerCase();
            return (
              <li key={favorite.id} className="shrink-0">
                <button
                  type="button"
                  onClick={() => onOpen(favorite.platform, favorite.username)}
                  aria-label={describe(favorite)}
                  aria-current={isActive ? "true" : undefined}
                  title={
                    favorite.checkError
                      ? `Gagal dicek: ${favorite.checkError}`
                      : undefined
                  }
                  className={`flex w-20 flex-col items-center gap-1.5 rounded-md p-1 ${focusRing}`}
                >
                  <span className="relative">
                    <span
                      className={`block h-14 w-14 overflow-hidden rounded-full ring-offset-2 ${
                        hasUpdate
                          ? "ring-[3px] ring-blue-600"
                          : "ring-1 ring-gray-300"
                      }`}
                    >
                      <Avatar favorite={favorite} />
                    </span>
                    <span
                      aria-hidden="true"
                      className="absolute -bottom-1 -right-1 flex h-6 w-6 items-center justify-center rounded-full border-2 border-white bg-gray-900 text-white"
                    >
                      {favorite.platform === "instagram" ? (
                        <Instagram className="h-3.5 w-3.5" />
                      ) : (
                        <TikTokIcon className="h-3.5 w-3.5" />
                      )}
                    </span>
                  </span>
                  <span
                    aria-hidden="true"
                    className={`w-full truncate text-center text-xs ${
                      isActive || hasUpdate
                        ? "font-semibold text-gray-900"
                        : "text-gray-700"
                    }`}
                  >
                    {favorite.username}
                  </span>
                  {hasUpdate && (
                    <span
                      aria-hidden="true"
                      className="w-full truncate text-center text-xs font-semibold text-blue-700"
                    >
                      {updateNotes(favorite).join(", ")}
                    </span>
                  )}
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
};

export default FavoritesBar;
