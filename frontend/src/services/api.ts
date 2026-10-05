import { WatermarkSettings } from '../app/components/WatermarkCustomizer';

// In development Vite proxies the API routes to localhost:3000 (see vite.config.ts).
// For other environments set VITE_API_BASE_URL to the deployed backend origin.
const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '');

export const AUTH_TOKEN_STORAGE_KEY = 'photoguard-auth-token';
export const UNAUTHORIZED_EVENT = 'photoguard:unauthorized';

const API_ENDPOINTS = {
  signup: '/auth/signup',
  login: '/auth/login',
  logout: '/auth/logout',
  upload: '/upload',
  watermark: '/watermark',
  images: '/images',
  verify: '/verify',
  image: (imageId: string) => `/image/${encodeURIComponent(imageId)}`,
  download: (imageId: string) => `/image/${encodeURIComponent(imageId)}/download`,
  share: (imageId: string) => `/image/${encodeURIComponent(imageId)}/share`,
  revoke: (imageId: string) => `/image/${encodeURIComponent(imageId)}/revoke`,
  proof: (imageId: string) => `/image/${encodeURIComponent(imageId)}/proof`,
};

export class ApiError extends Error {
  status?: number;

  constructor(message: string, status?: number) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

type AuthResponse = {
  token?: string;
  user?: { fullName?: string; username?: string };
};

export type RemoteImage = {
  id: string;
  imageId: string;
  url: string;
  name: string;
  size: string;
  uploadedAt: string;
  owner: string;
  watermarked: boolean;
  visibleWatermark?: string | null;
  invisibleWatermark?: string | null;
  hashFingerprint: string;
  metadataRemoved: string[];
  shared: boolean;
  shareUrl?: string | null;
};

export type VerifyResult = {
  sha256: string;
  protected: boolean;
  exactMatch: { owner: string; registeredAt: string } | null;
  watermark: { text: string; registeredOwner: string | null; registeredAt: string | null } | null;
};

function apiUrl(path: string) {
  return `${API_BASE_URL}${path}`;
}

function authHeaders(): Record<string, string> {
  const token = window.localStorage.getItem(AUTH_TOKEN_STORAGE_KEY);
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function request(path: string, init: RequestInit = {}, { auth = true } = {}): Promise<Response> {
  let response: Response;
  try {
    response = await fetch(apiUrl(path), {
      ...init,
      headers: { ...(auth ? authHeaders() : {}), ...(init.headers ?? {}) },
    });
  } catch {
    throw new ApiError("Can't reach the PhotoGuard server. Is the backend running on port 3000?");
  }

  if (!response.ok) {
    let message = `Request failed with status ${response.status}`;
    try {
      const body = await response.json();
      if (typeof body.detail === 'string') message = body.detail;
      else if (Array.isArray(body.detail) && body.detail[0]?.msg) message = body.detail[0].msg;
    } catch {
      // keep the generic message
    }
    if (response.status === 401 && auth) {
      window.dispatchEvent(new CustomEvent(UNAUTHORIZED_EVENT, { detail: message }));
    }
    throw new ApiError(message, response.status);
  }
  return response;
}

async function json<T>(path: string, init: RequestInit = {}, options = {}): Promise<T> {
  const response = await request(path, init, options);
  return response.json() as Promise<T>;
}

// POST /auth/signup
export function signUpUser(input: { fullName: string; username: string; password: string }) {
  return json<AuthResponse>(API_ENDPOINTS.signup, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  }, { auth: false });
}

// POST /auth/login
export function loginUser(input: { username: string; password: string }) {
  return json<AuthResponse>(API_ENDPOINTS.login, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  }, { auth: false });
}

// POST /auth/logout — invalidates the token on the server
export async function logoutUser() {
  await request(API_ENDPOINTS.logout, { method: 'POST' }).catch(() => {});
}

// GET /images — the signed-in user's vault
export async function listImages(): Promise<RemoteImage[]> {
  const body = await json<{ images: RemoteImage[] }>(API_ENDPOINTS.images);
  return body.images;
}

// POST /upload — server adds the invisible watermark, strips metadata, fingerprints
export function uploadImage(file: File) {
  const formData = new FormData();
  formData.append('file', file);
  return json<RemoteImage>(API_ENDPOINTS.upload, { method: 'POST', body: formData });
}

// POST /watermark — updates the same image in place
export function watermarkImage(imageId: string, settings: WatermarkSettings) {
  return json<RemoteImage>(API_ENDPOINTS.watermark, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ imageId, settings }),
  });
}

// GET /image/:id/download
export async function downloadImage(imageId: string) {
  const response = await request(API_ENDPOINTS.download(imageId));
  return response.blob();
}

// DELETE /image/:id
export async function deleteImage(imageId: string): Promise<void> {
  await request(API_ENDPOINTS.image(imageId), { method: 'DELETE' });
}

// POST /image/:id/share — creates a new revocable link
export async function shareImage(imageId: string): Promise<string> {
  const body = await json<{ shareUrl: string }>(API_ENDPOINTS.share(imageId), { method: 'POST' });
  return body.shareUrl;
}

// POST /image/:id/revoke — deletes every share link for the image
export async function revokeImage(imageId: string): Promise<number> {
  const body = await json<{ linksRevoked: number }>(API_ENDPOINTS.revoke(imageId), { method: 'POST' });
  return body.linksRevoked;
}

// GET /image/:id/proof — stored SHA-256 fingerprint and creation timestamp
export function getProofOfCreation(imageId: string) {
  return json<{ hash: string; createdAt: string; owner: string }>(API_ENDPOINTS.proof(imageId));
}

// POST /verify — check any image for a PhotoGuard mark (no sign-in needed)
export function verifyImage(file: File) {
  const formData = new FormData();
  formData.append('file', file);
  return json<VerifyResult>(API_ENDPOINTS.verify, { method: 'POST', body: formData }, { auth: false });
}
