const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });

  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    throw new ApiError(res.status, body.detail ?? "Request failed");
  }

  return res.json() as Promise<T>;
}

export interface TokenResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
}

export interface UserResponse {
  id: string;
  tenant_id: string;
  email: string;
  full_name: string;
  role: string;
}

export function login(organization_slug: string, email: string, password: string) {
  return request<TokenResponse>("/api/v1/auth/login", {
    method: "POST",
    body: JSON.stringify({ organization_slug, email, password }),
  });
}

export function register(organization_name: string, full_name: string, email: string, password: string) {
  return request<{ organization_slug: string; user: UserResponse; tokens: TokenResponse }>(
    "/api/v1/auth/register",
    {
      method: "POST",
      body: JSON.stringify({ organization_name, full_name, email, password }),
    }
  );
}

export function getCurrentUser(accessToken: string) {
  return request<UserResponse>("/api/v1/users/me", {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
}
