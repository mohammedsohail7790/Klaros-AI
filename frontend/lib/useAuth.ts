"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { ApiError, getCurrentUser, UserResponse } from "@/lib/api";

export function useAuth() {
  const router = useRouter();
  const [token, setToken] = useState<string | null>(null);
  const [user, setUser] = useState<UserResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const stored = sessionStorage.getItem("klaros_access_token");
    if (!stored) {
      router.push("/login");
      return;
    }
    setToken(stored);
    getCurrentUser(stored)
      .then((u) => {
        setUser(u);
        setLoading(false);
      })
      .catch((err) => {
        setLoading(false);
        // Only a genuine authentication failure (the API already tried a silent refresh) ends the
        // session. A network blip or a 5xx must not log the user out of a valid session.
        if (err instanceof ApiError && (err.status === 401 || err.status === 403)) {
          sessionStorage.removeItem("klaros_access_token");
          setError("Session expired. Please sign in again.");
          router.push("/login");
        } else {
          setError("We couldn't reach Klaros just now. Check your connection and reload the page.");
        }
      });
  }, [router]);

  return { token, user, loading, error };
}
