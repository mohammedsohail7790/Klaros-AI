"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { getCurrentUser, UserResponse } from "@/lib/api";

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
      .catch(() => {
        sessionStorage.removeItem("klaros_access_token");
        setError("Session expired. Please sign in again.");
        setLoading(false);
        router.push("/login");
      });
  }, [router]);

  return { token, user, loading, error };
}
