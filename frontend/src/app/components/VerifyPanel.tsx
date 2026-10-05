import { useRef, useState } from 'react';
import { CheckCircle, FileSearch, Fingerprint, ShieldAlert, ShieldCheck, Upload } from 'lucide-react';
import { VerifyResult, verifyImage } from '../../services/api';

function formatDate(iso: string | null | undefined) {
  return iso ? new Date(iso).toLocaleString() : 'unknown date';
}

export function VerifyPanel() {
  const inputRef = useRef<HTMLInputElement>(null);
  const [fileName, setFileName] = useState('');
  const [preview, setPreview] = useState<string | null>(null);
  const [result, setResult] = useState<VerifyResult | null>(null);
  const [error, setError] = useState('');
  const [checking, setChecking] = useState(false);
  const [dragging, setDragging] = useState(false);

  const check = async (file: File | undefined) => {
    if (!file) return;
    setFileName(file.name);
    setPreview(prev => {
      if (prev) URL.revokeObjectURL(prev);
      return URL.createObjectURL(file);
    });
    setResult(null);
    setError('');
    setChecking(true);
    try {
      setResult(await verifyImage(file));
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Verification failed.');
    } finally {
      setChecking(false);
    }
  };

  return (
    <div className="space-y-6">
      <div
        onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => { e.preventDefault(); setDragging(false); check(e.dataTransfer.files[0]); }}
        className={`rounded-lg border-2 border-dashed p-10 text-center transition ${
          dragging ? 'border-primary bg-primary/5' : 'border-border bg-card'
        }`}
      >
        <FileSearch className="mx-auto mb-3 h-10 w-10 text-muted-foreground" />
        <p className="text-foreground mb-1">Drop any image here to check who it belongs to</p>
        <p className="text-sm text-muted-foreground mb-4">
          Works on screenshots of the vault file, re-saved JPEGs and copies downloaded from elsewhere.
        </p>
        <button
          onClick={() => inputRef.current?.click()}
          className="inline-flex items-center gap-2 px-4 py-2 rounded bg-primary text-primary-foreground hover:opacity-90"
        >
          <Upload className="h-4 w-4" />
          Choose image
        </button>
        <input
          ref={inputRef}
          type="file"
          accept="image/jpeg,image/png,image/webp"
          className="hidden"
          onChange={(e) => { check(e.target.files?.[0]); e.target.value = ''; }}
        />
      </div>

      {(checking || result || error) && (
        <div className="rounded-lg border border-border bg-card p-6">
          <div className="flex flex-col gap-6 md:flex-row">
            {preview && (
              <img src={preview} alt={fileName} className="h-40 w-40 shrink-0 rounded object-cover bg-muted" />
            )}

            <div className="min-w-0 flex-1 space-y-4">
              <p className="text-sm text-muted-foreground truncate">{fileName}</p>

              {checking && <p className="text-foreground animate-pulse">Scanning for a hidden ownership mark…</p>}

              {error && (
                <div className="flex items-start gap-3 text-destructive">
                  <ShieldAlert className="h-5 w-5 shrink-0" />
                  <p className="text-sm">{error}</p>
                </div>
              )}

              {result && (
                <>
                  <div className="flex items-center gap-3">
                    {result.protected ? (
                      <ShieldCheck className="h-7 w-7 text-green-600" />
                    ) : (
                      <ShieldAlert className="h-7 w-7 text-yellow-600" />
                    )}
                    <h3 className="text-foreground">
                      {result.protected ? 'PhotoGuard protection found' : 'No PhotoGuard mark found'}
                    </h3>
                  </div>

                  {result.watermark && (
                    <div className="rounded-lg border border-border p-4">
                      <p className="text-sm text-foreground mb-1 flex items-center gap-2">
                        <CheckCircle className="h-4 w-4 text-green-600" /> Hidden watermark
                      </p>
                      <p className="text-sm text-muted-foreground">
                        Reads <span className="font-mono text-foreground">"{result.watermark.text}"</span>
                        {result.watermark.registeredOwner && (
                          <> — registered to <strong className="text-foreground">{result.watermark.registeredOwner}</strong> on {formatDate(result.watermark.registeredAt)}</>
                        )}
                        .
                      </p>
                    </div>
                  )}

                  <div className="rounded-lg border border-border p-4">
                    <p className="text-sm text-foreground mb-1 flex items-center gap-2">
                      <Fingerprint className="h-4 w-4" /> SHA-256 fingerprint
                    </p>
                    <p className="text-xs font-mono text-muted-foreground break-all mb-2">{result.sha256}</p>
                    <p className="text-sm text-muted-foreground">
                      {result.exactMatch
                        ? <>Exact match: this is the original file registered by <strong className="text-foreground">{result.exactMatch.owner}</strong> on {formatDate(result.exactMatch.registeredAt)}.</>
                        : result.watermark
                          ? 'No exact match — this copy has been edited or re-saved since it was protected, but the hidden mark survived.'
                          : 'This file does not match anything in PhotoGuard.'}
                    </p>
                  </div>
                </>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
