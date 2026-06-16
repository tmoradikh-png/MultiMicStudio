"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { api, clearToken, getToken, type ProjectListItem } from "@/lib/api";

export default function ProjectsPage() {
  const router = useRouter();
  const [items, setItems] = useState<ProjectListItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      const data = await api.listProjects();
      setItems(data);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load projects");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!getToken()) {
      router.replace("/");
      return;
    }
    load();
    // Poll so processing status updates without a manual refresh.
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, [load, router]);

  function signOut() {
    clearToken();
    router.replace("/");
  }

  return (
    <div className="container">
      <div className="header">
        <div>
          <h1 className="title">Projects</h1>
          <p className="subtitle">Your multi-phone recording sessions.</p>
        </div>
        <button className="button ghost" onClick={signOut}>
          Sign out
        </button>
      </div>

      {error ? <p className="error">{error}</p> : null}
      {loading ? <p className="subtitle">Loading…</p> : null}

      {!loading && items.length === 0 ? (
        <div className="card">
          <p className="subtitle">
            No sessions yet. Create one in the mobile app, record, then process it.
          </p>
        </div>
      ) : null}

      {items.map((item) => {
        const status = item.processing_status ?? item.status;
        return (
          <Link
            key={item.session_id}
            href={`/projects/${item.session_id}`}
            className="card"
            style={{ display: "block" }}
          >
            <div className="row">
              <div>
                <strong style={{ fontSize: 17 }}>{item.title}</strong>
                <p className="subtitle" style={{ margin: "4px 0 0" }}>
                  {new Date(item.created_at).toLocaleString()}
                </p>
              </div>
              <span className={`badge ${status}`}>{status}</span>
            </div>
          </Link>
        );
      })}
    </div>
  );
}
