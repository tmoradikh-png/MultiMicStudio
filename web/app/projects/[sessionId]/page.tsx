"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { absoluteUrl, api, getToken, type EnhancementMode, type Project } from "@/lib/api";

const ENHANCEMENT_MODES: { id: EnhancementMode; label: string; hint: string }[] = [
  { id: "natural", label: "Natural Stereo", hint: "True left/right separation, minimal processing (reference)" },
  { id: "studio_voice", label: "Studio Voice", hint: "Cleaner, fuller voice — EQ, leveling, noise reduction" },
  { id: "karaoke", label: "Singing / Karaoke", hint: "Vocal reverb and light echo" },
  { id: "party", label: "Party / Room", hint: "Wider stereo and more room ambience" },
];

export default function ProjectDetailPage() {
  const router = useRouter();
  const params = useParams<{ sessionId: string }>();
  const sessionId = params.sessionId;
  const [project, setProject] = useState<Project | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notReady, setNotReady] = useState(false);
  const [enhancing, setEnhancing] = useState<EnhancementMode | null>(null);

  const load = useCallback(async () => {
    try {
      const data = await api.getProject(sessionId);
      setProject(data);
      setNotReady(false);
      setError(null);
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

  async function onEnhance(mode: EnhancementMode) {
    setEnhancing(mode);
    setError(null);
    try {
      const updated = await api.enhanceProject(sessionId, mode);
      setProject(updated);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not apply enhancement");
    } finally {
      setEnhancing(null);
    }
  }

  const audioUrl = absoluteUrl(project?.final_audio_url ?? null);
  const stereoUrl = absoluteUrl(project?.final_audio_stereo_url ?? null);
  const enhancedUrl = absoluteUrl(project?.final_audio_enhanced_url ?? null);
  const activeMode = (project?.enhancement_mode ?? "natural") as EnhancementMode;
  const stems = project?.stems ?? [];
  const status = project?.processing_status ?? (notReady ? "pending" : "");

  return (
    <div className="container">
      <div className="header">
        <Link href="/projects" className="button ghost">
          ← Back
        </Link>
        {status ? <span className={`badge ${status}`}>{status}</span> : null}
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

      {stereoUrl ? (
        <div className="card">
          <strong>Final mix — stereo (Phone A left · Phone B right)</strong>
          <audio controls src={stereoUrl} />
          <p style={{ marginTop: 10 }}>
            <a href={stereoUrl} download>
              Download stereo mix
            </a>
          </p>
        </div>
      ) : null}

      {stereoUrl ? (
        <div className="card">
          <strong>Audio enhancement</strong>
          <p className="subtitle" style={{ marginTop: 6 }}>
            Optional presets applied on top of the natural stereo mix above. The
            natural mix is always kept for comparison; effects never change the
            timing or stereo position.
          </p>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginTop: 12 }}>
            {ENHANCEMENT_MODES.map((m) => {
              const isActive = activeMode === m.id;
              return (
                <button
                  key={m.id}
                  className={`button ${isActive ? "" : "ghost"}`}
                  disabled={enhancing !== null}
                  onClick={() => onEnhance(m.id)}
                  title={m.hint}
                >
                  {enhancing === m.id ? "Applying…" : m.label}
                </button>
              );
            })}
          </div>
          <p className="subtitle" style={{ marginTop: 10 }}>
            {ENHANCEMENT_MODES.find((m) => m.id === activeMode)?.hint}
          </p>
          {enhancedUrl && activeMode !== "natural" ? (
            <div style={{ marginTop: 12 }}>
              <strong>Enhanced mix — {ENHANCEMENT_MODES.find((m) => m.id === activeMode)?.label}</strong>
              <audio controls src={enhancedUrl} />
              <p style={{ marginTop: 10 }}>
                <a href={enhancedUrl} download>
                  Download enhanced mix
                </a>
              </p>
            </div>
          ) : null}
        </div>
      ) : null}

      {audioUrl ? (
        <div className="card">
          <strong>Final mix — mono</strong>
          <audio controls src={audioUrl} />
          <p style={{ marginTop: 10 }}>
            <a href={audioUrl} download>
              Download mono mix
            </a>
          </p>
        </div>
      ) : null}

      {stems.length ? (
        <div className="card">
          <strong>Individual tracks (stems)</strong>
          {stems.map((s, i) => {
            const url = absoluteUrl(s.content);
            return (
              <div key={s.id} style={{ marginTop: 10 }}>
                <span className="subtitle">Track {i + 1}</span>
                {url ? <audio controls src={url} /> : null}
                {url ? (
                  <p style={{ marginTop: 6 }}>
                    <a href={url} download>
                      Download track {i + 1}
                    </a>
                  </p>
                ) : null}
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
