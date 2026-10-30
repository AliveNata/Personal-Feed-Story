import React, { useRef, useState } from "react";
import { Folder } from "../types";
import { ApiError, createFolder } from "../utils/api";
import { focusRing, primaryButton, secondaryButton } from "./ui";

interface NewFolderFormProps {
  onCreated: (folder: Folder) => void;
  onCancel: () => void;
}

const NewFolderForm: React.FC<NewFolderFormProps> = ({
  onCreated,
  onCancel,
}) => {
  const [name, setName] = useState("");
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    setIsSaving(true);
    setError(null);
    try {
      onCreated(await createFolder(name));
    } catch (caught) {
      console.error("createFolder failed", caught);
      setError(
        caught instanceof ApiError ? caught.message : "Gagal membuat folder",
      );
      setIsSaving(false);
      // The submit button was disabled while saving, which drops focus: give it back to the field.
      requestAnimationFrame(() => inputRef.current?.focus());
    }
  };

  return (
    <form
      onSubmit={handleSubmit}
      onKeyDown={(event) => {
        if (event.key === "Escape") onCancel();
      }}
      className="mb-4 rounded-lg bg-white p-4 shadow-md"
    >
      <label
        htmlFor="new-folder-name"
        className="mb-1 block text-sm font-semibold text-gray-700"
      >
        Nama folder baru
      </label>
      <div className="flex flex-col gap-2 sm:flex-row">
        <input
          id="new-folder-name"
          value={name}
          ref={inputRef}
          onChange={(event) => setName(event.target.value)}
          aria-invalid={error !== null}
          aria-describedby={error ? "new-folder-error" : undefined}
          maxLength={100}
          required
          autoFocus
          autoComplete="off"
          placeholder="contoh: Liburan Bali"
          className={`min-h-[44px] flex-1 rounded-md border border-gray-300 px-3 text-gray-900 ${focusRing}`}
        />
        <div className="flex gap-2">
          <button
            type="submit"
            className={`flex-1 sm:flex-none ${primaryButton}`}
            disabled={isSaving || !name.trim()}
          >
            {isSaving ? "Membuat..." : "Buat folder"}
          </button>
          <button
            type="button"
            className={`flex-1 sm:flex-none ${secondaryButton}`}
            onClick={onCancel}
          >
            Batal
          </button>
        </div>
      </div>
      {error && (
        <p
          id="new-folder-error"
          role="alert"
          className="mt-2 text-sm text-red-700"
        >
          {error}
        </p>
      )}
    </form>
  );
};

export default NewFolderForm;
