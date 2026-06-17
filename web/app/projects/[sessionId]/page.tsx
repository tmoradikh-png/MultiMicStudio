"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import {
  absoluteUrl,
  api,
  getToken,
  type OutputItem,
  type Project,
  type ProjectOutputs,
} from "@/lib/api";

export default function ProjectDetailPage() {
  const router = useRouter();
  const params = useParams<{ sessionId: string }>();
  const sessionId = params.sessionId;
  const [project, setProject] = useState<Project | null>(null);
  const [outputs, setOutputs] = useState<ProjectOutputs | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notReady, setNotReady] = useState(false);
  const [shared, setShared] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const data = await api.getProject(sessionId);
      setProject(data);
      setNotReady(false);
      setError(null);
      if (data.processing_status === "done") {
        try {
          setOutputs(await api.getOutputs(sessionId));
        } catch {
          /* outputs are best-effort; the rest of the page still renders */
        }
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Could not load project";
      // 404 => project not processed yet; keep polling quietly.
      if (msg.toLowerCase().includes("not processed")) {
        setNotReady(true);
      } else {
        setError(msg);
      }
    }
  }, [sessionId]);

  useEffect(() => {
    if (!getToken()) {
      router.replace("/");
      return;
    }
    load();
    const t = setInterval(load, 4000);
    return () => clearInterval(t);
  }, [load, router]);

  async function onProcess() {
    try {
      await api.processSession(sessionId);
      setNotReady(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not start processing");
    }
  }

  async function onShare(item: OutputItem) {
    const url = absoluteUrl(item.url);
    if (!url) return;
    const shareData = {
      title: `MultiMic — ${item.label}`,
      text: `${item.label} from this recording session`,
      url,
    };
    try {
      if (typeof navigator !== "undefined" && navigator.share) {
        await navigator.share(shareData);
        return;
      }
      if (typeof navigator !== "undefined" && navigator.clipboard) {
        await navigator.clipboard.writeText(url);
        setShared(item.role);
        setTimeout(() => setShared((r) => (r === item.role ? null : r)), 1800);
      }
    } catch {
      /* user cancelled share or clipboard blocked — no-op */
    }
  }

  const status = project?.processing_status ?? (notReady ? "pending" : "");
  const quality = outputs?.quality ?? null;

  return (
    <div className="container">
      <div className="header">
        <Link href="/projects" className="button ghost">
          ← Back
        </Link>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          {quality ? (
            <span
              className={`badge ${quality.ok ? "pass" : "fail"}`}
              title={`${quality.passed}/${quality.total} quality checks passed${
                quality.baseline_failed
                  ? `, ${quality.baseline_failed} below baseline`
                  : ""
              }`}
            >
              {quality.ok ? "Quality: PASS" : "Quality: REVIEW"}
            </span>
          ) : null}
          {status ? <span className={`badge ${status}`}>{status}</span> : null}
        </div>
      </div>

      <h1 className="title">Project</h1>
      <p className="subtitle">Session {sessionId}</p>

      {error ? <p className="error">{error}</p> : null}

      {notReady ? (
        <div className="card">
          <p className="subtitle">
            This session has not been processed yet. Make sure every phone has
            uploaded, then start mixing & transcription.
          </p>
          <button className="button" onClick={onProcess}>
            Mix &amp; transcribe
          </button>
        </div>
      ) : null}

      {project?.processing_status === "processing" ? (
        <div className="card">
          <p className="subtitle">Aligning, mixing and transcribing… this updates automatically.</p>
        </div>
      ) : null}

      {project?.processing_status === "failed" ? (
        <div className="card">
          <p className="error">Processing failed: {project.error}</p>
          <button className="button" onClick={onProcess}>
            Retry
          </button>
        </div>
      ) : null}

      {/* Quality summary — reuses the QA bench checks (same as the bench report). */}
      {quality ? (
        <div className="card">
          <div className="row">
            <strong>Quality check</strong>
            <span className={`badge ${quality.ok ? "pass" : "fail"}`}>
              {quality.passed}/{quality.total} checks
              {quality.baseline_failed
                ? ` · ${quality.baseline_failed} below baseline`
                : " · baseline OK"}
            </span>
          </div>
          <p className="subtitle" style={{ marginTop: 6 }}>
            {quality.ok
              ? "This recording meets the saved quality baseline — good to share."
              : "Some checks need review before this is demo-ready (see below)."}
          </p>
          {quality.summary.length ? (
            <ul className="qa-list">
              {quality.summary.map((s, i) => (
                <li key={i}>
                  <span className={`qa-mark ${s.good ? "good" : "bad"}`}>
                    {s.good ? "✓" : "!"}
                  </span>
                  <span className="qa-text">
                    <span>{s.question}</span>
                    {s.detail ? (
                      <span className="qa-detail">{s.detail}</span>
                    ) : null}
                  </span>
                  <strong>{s.answer}</strong>
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}

      {/* All output roles: raw phones, natural stereo, presets, mono down-mix. */}
      {outputs ? (
        <div className="card">
          <strong>Outputs</strong>
          <p className="subtitle" style={{ marginTop: 6 }}>
            Every recording and mix from this session. Natural stereo is the
            reference; the presets are applied on top without changing timing or
            stereo position.
          </p>
          {outputs.outputs.map((o) => {
            const url = absoluteUrl(o.url);
            return (
              <div
                key={o.role}
                className="card"
                style={{ marginTop: 12, background: "transparent" }}
              >
                <div className="output-head">
                  <strong>{o.label}</strong>
                  <span className="tag">{o.kind === "raw" ? "Raw phone" : "Mix"}</span>
                </div>
                {url && o.available ? (
                  <>
                    <audio controls preload="metadata" src={url} />
                    <div className="output-actions" style={{ marginTop: 10 }}>
                      <a className="button ghost small" href={url} download>
                        Download
                      </a>
                      <button
                        className="button ghost small"
                        onClick={() => onShare(o)}
                      >
                        {shared === o.role ? "Link copied ✓" : "Share"}
                      </button>
                    </div>
                  </>
                ) : (
                  <p className="unavailable">Not available for this session.</p>
                )}
              </div>
            );
          })}
        </div>
      ) : null}

      {project?.transcript_text ? (
        <div className="card">
          <div className="row">
            <strong>Transcript</strong>
            <a
              href={`data:text/plain;charset=utf-8,${encodeURIComponent(
                project.transcript_text,
              )}`}
              download="transcript.txt"
            >
              Download transcript
            </a>
          </div>
          <p className="transcript" style={{ marginTop: 12 }}>
            {project.transcript_text}
          </p>
        </div>
      ) : null}
    </div>
  );
}
