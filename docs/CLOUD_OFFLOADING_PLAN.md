# Cloud Offloading Plan: Parallel SWMM Simulations for openswmm.mcp and openswmm.gymnasium

**Status:** Decision doc + roadmap (pre-implementation)
**Date:** 2026-06-11
**Scope:** Offload simulations in parallel to AWS, Azure, GCP, and on-prem/HPC; retrieve results; burst from local. Platform-agnostic by design.

---

## 1. Grounding: What Exists Today

| Fact | Source |
|---|---|
| Engine is in-process, handle-based (`openswmm.engine.Solver` v6, thread-safe; GIL released during `step()`) | `openswmm.gymnasium/src/openswmm_gymnasium/_engine/solver_adapter.py` |
| Gymnasium envs delegate parallelism to `SyncVectorEnv`/`AsyncVectorEnv`; no Ray/RPC abstractions | `openswmm.gymnasium/docs/IMPLEMENTATION_PLAN.md` §2.3 |
| Per-step payloads are tiny: obs ~tens of bytes, actions a few floats. Compute, not I/O, is the bottleneck | TwinTank benchmark, `envs/base.py` |
| MCP server: FastMCP 3.x, stdio/HTTP/SSE, 358 tools; `SessionManager` with async locks, isolated working dir per session, all engine calls via `asyncio.to_thread()` | `openswmm.mcp/src` |
| Portable artifacts already exist: `.inp` (input), `.out` (binary results), `.rpt` (report), `.gpkg` (multi-run queries), hotstart files (state checkpoints), JSONL trajectories | both repos |
| No queue/job/remote-execution hooks exist anywhere yet | both repos |

Three implications drive everything below:

1. **The solver cannot be split from its process.** Offloading means shipping the *whole simulation* (or whole episode/rollout) to a remote worker, never individual engine calls.
2. **Hotstart files are a portable state checkpoint.** They enable session migration, episode seeding, and resumable jobs across machines.
3. **Everything needed for a run is already file-shaped** (`.inp`, hotstart, forcing timeseries → `.out`, `.rpt`, `.gpkg`, JSONL). This makes blob storage a natural data plane.

---

## 2. Three Workloads, Three Shapes

| Workload | Shape | Latency tolerance | Statefulness |
|---|---|---|---|
| **Batch scenario sweeps** (Monte Carlo, design storms, calibration) | Embarrassingly parallel, fire-and-forget | Minutes–hours fine | Stateless per run |
| **RL training rollouts** (gymnasium) | Long-lived `env.step()` loops, thousands of tiny interactions per episode | Per-step RPC over WAN (10–100 ms RTT) is **fatal** vs. sub-ms local step | Stateful per episode |
| **Interactive MCP sessions** (LLM-driven stepping/forcing) | Long-lived, sparse human/LLM-paced calls | Seconds fine | Stateful, sticky |

A single architecture serves all three **only if** the unit of offload differs per workload: whole *runs* for batch, whole *episodes/rollouts* for RL (policy colocated with env), whole *sessions* for interactive. Never offload individual steps across a WAN.

---

## 3. Architecture Overview

Separate three concerns; each is independently swappable:

```
┌─────────────────────────────────────────────────────────────┐
│ CLIENTS                                                      │
│  openswmm.mcp tools        openswmm.gymnasium trainer        │
└────────────┬─────────────────────────┬───────────────────────┘
             │ submit/status/fetch     │ rollout dispatch
┌────────────▼─────────────────────────▼───────────────────────┐
│ CONTROL PLANE  (pluggable Executor backends)                  │
│  local subprocess │ Kubernetes │ managed batch │ Ray │ SLURM  │
└────────────┬──────────────────────────────────────────────────┘
             │ runs containers of
┌────────────▼──────────────────────────────────────────────────┐
│ EXECUTION UNIT: one OCI container image                        │
│  openswmm.engine + thin runner (batch mode | session-server    │
│  mode | rollout-worker mode)                                   │
└────────────┬──────────────────────────────────────────────────┘
             │ reads/writes run bundles & result manifests via
┌────────────▼──────────────────────────────────────────────────┐
│ DATA PLANE: object storage behind one interface (fsspec)       │
│  S3 │ Azure Blob │ GCS │ MinIO (on-prem) │ local filesystem    │
└────────────────────────────────────────────────────────────────┘
```

The platform-agnostic contract is the **run bundle** (inputs) and **result manifest** (outputs) — plain files with a JSON manifest. Any control plane that can run a container and reach the data plane works.

### 3.1 Run bundle and result manifest

```
bundle/                        results/
  manifest.json                  manifest.json   (status, timings, hashes, engine version)
  model.inp                      model.out
  hotstart.hsf      (optional)   model.rpt
  forcing/*.csv     (optional)   trajectory.jsonl  (RL/recorded runs)
  overrides.json    (optional)   results.gpkg      (optional)
```

Bundles are content-addressed (hash of contents) → free deduplication and caching: a 1,000-run sweep over one model uploads the `.inp` once.

---

## 4. Control-Plane Approaches: Pros and Cons

### A. Kubernetes (Jobs API + Kueue or Argo Workflows) — *yes, it applies here*

Run the simulation container as K8s `Jobs`; Kueue provides queueing/fair-sharing/quotas, Argo provides DAG workflows (e.g., calibrate → validate → sweep). Identical manifests run on EKS (AWS), AKS (Azure), GKE (GCP), k3s/kubeadm (on-prem).

| Pros | Cons |
|---|---|
| **Most platform-agnostic option that exists.** One YAML/API for all four targets | Operational complexity: cluster lifecycle, upgrades, networking — real even with managed K8s |
| Handles all three workloads: Jobs (batch), Deployments+Services (interactive sessions), KubeRay (RL) | Overkill below ~50 concurrent runs; a queue+VM setup is simpler at small scale |
| Cluster autoscaler + spot/preemptible node pools → cost-efficient thousands-scale | Cold cluster scale-up takes 1–3 min per node; not instant burst |
| Kueue gives quotas, priorities, gang scheduling natively | K8s API is a large dependency surface for the client libraries |
| On-prem story is first-class (k3s on lab hardware) | Requires container registry reachable from every cluster |
| Huge ecosystem: monitoring, secrets, CSI storage drivers | |

**Verdict:** Best fit as the *primary* portable backend at the stated scale (thousands+, four target environments). Mitigate complexity by treating K8s purely as a job runner — no service meshes, no operators beyond Kueue/KubeRay.

### B. Managed batch services (AWS Batch, Azure Batch, GCP Batch)

| Pros | Cons |
|---|---|
| Zero cluster management; provider handles scheduling and scaling | **Three different APIs, quotas, and semantics** → you write and maintain three adapters anyway |
| Deep spot integration, often cheapest raw compute | No on-prem story at all |
| Good for very large array jobs (AWS Batch array jobs to 10k+) | Weak fit for stateful/interactive workloads (batch-only) |
| | Per-provider quirks leak through any abstraction (retry semantics, log access, job size limits) |

**Verdict:** Viable *secondary* backends behind the same `Executor` interface for teams already committed to one cloud. Not the portability layer itself.

### C. Ray (KubeRay on K8s, or Ray clusters on raw VMs)

| Pros | Cons |
|---|---|
| **Purpose-built for the RL workload**: RLlib rollout workers colocate policy + env, exactly the right offload unit | Brings a heavy framework; version coupling between cluster and client |
| Ray Jobs API doubles as a batch submitter | Its own cluster to operate (unless via KubeRay, which then presumes K8s anyway) |
| Runs on all clouds + on-prem | Redundant with K8s+Kueue for plain batch sweeps |
| Object store handles in-cluster data movement | Less natural fit for interactive MCP sessions |

**Verdict:** Adopt *for the RL workload only*, deployed via KubeRay on the same clusters. Don't make it the universal control plane.

### D. Serverless containers (Cloud Run, Azure Container Apps, Fargate, Lambda)

| Pros | Cons |
|---|---|
| Zero idle cost; instant-ish scale-out for short runs | **Hard runtime ceilings** (Lambda 15 min; Cloud Run jobs better but capped) — long continuous simulations get killed |
| Minimal ops | Three different APIs; no on-prem |
| | Cold starts; constrained CPU/memory shapes |
| | No sticky long-lived sessions for interactive work (or expensive to force) |

**Verdict:** Reject as primary. Optionally revisit for short-run sweep bursts later.

### E. DIY queue + container workers (Redis/NATS/RabbitMQ + Celery/Arq + VM fleet)

| Pros | Cons |
|---|---|
| Simplest mental model; full control; great for "tens" scale | You own autoscaling, retries, dead-letter handling, worker health — reinventing Kueue |
| Cheap on a few reserved VMs or lab machines | Scaling to thousands means rebuilding a scheduler badly |
| Works anywhere a VM and a broker exist | Another stateful service (broker) to operate |

**Verdict:** Reject as the cloud backend. However, its *shape* (submit → queue → worker → results) is exactly the `Executor` interface, and the **local backend** (subprocess pool, no broker) is the first implementation and the burst-from-local fallback.

### F. HPC schedulers (SLURM)

| Pros | Cons |
|---|---|
| Standard on university/agency clusters Caleb's likely to encounter; massive free-at-point-of-use compute | Batch-only; no interactive sessions; queue wait times |
| `sbatch` array jobs map 1:1 onto run bundles | Containers via Apptainer/Singularity, not Docker — image needs conversion |
| | No autoscaling; fixed allocation |

**Verdict:** Add as an `Executor` backend for batch sweeps where HPC access exists. Cheap to support because the run-bundle contract does the heavy lifting.

---

## 5. Data-Plane Approaches: Pros and Cons

### A. Object/blob storage behind `fsspec` (S3, Azure Blob, GCS, MinIO on-prem) — **recommended**

One code path (`fsspec` URLs: `s3://…`, `az://…`, `gs://…`, `file://…`); MinIO gives on-prem/HPC the same S3 API; local filesystem is just another backend for dev/test.

| Pros | Cons |
|---|---|
| True platform-agnosticism with one dependency | Eventual-consistency edge cases (largely historical now, but list-after-write on some backends) |
| Cheap at scale; lifecycle policies auto-expire old sweep outputs | Per-object latency (~10–100 ms) — irrelevant for file-sized artifacts, fatal for per-step data (don't use it for that) |
| Content-addressing → dedup of repeated `.inp`/forcing files across sweep members | Egress fees when results cross cloud boundaries — keep compute next to its bucket |
| Presigned URLs decouple workers from credentials | `fsspec` backend quirks differ subtly; needs a conformance test suite |

### B. Provider-native SDKs (boto3 / azure-storage-blob / google-cloud-storage) behind a hand-rolled adapter

| Pros | Cons |
|---|---|
| Full access to provider features (multipart tuning, storage classes) | Three SDKs to wrap, test, and version — exactly the maintenance burden `fsspec` already absorbed |
| Sometimes faster than generic layers | No on-prem story without writing a fourth adapter |

**Verdict:** Reject; `fsspec` covers the need. Drop to native SDKs per-backend only if profiling demands it.

### C. Shared filesystems (NFS, EFS, Azure Files, Filestore, Lustre)

| Pros | Cons |
|---|---|
| Engine code runs unmodified — paths just work | Provider-specific provisioning; poor cross-cloud portability |
| Good inside one HPC cluster (Lustre/GPFS already there) | Cost and throughput ceilings; locking semantics bite under thousands of writers |
| | Couples workers to a network mount → harder scheduling |

**Verdict:** Use only where it already exists (HPC scratch). Workers should stage bundle → local disk → run → upload, never simulate directly against network mounts.

### D. Streaming channels (gRPC/WebSocket) for live data

| Pros | Cons |
|---|---|
| Only way to get live progress, live observations, interactive stepping | A service to run, secure, and load-balance; not a storage system |
| Right transport for the interactive session server | Useless for batch retrieval |

**Verdict:** Not a data plane. Used narrowly as the *session protocol* for interactive mode (§6.3); final artifacts still land in blob storage.

---

## 6. Per-Workload Patterns

### 6.1 Batch sweeps (MCP `analysis_compare_scenarios`-style, calibration, Monte Carlo)

Client builds N run bundles → uploads (deduped) → submits N jobs via `Executor` → workers download bundle, run `Solver` start-to-end, upload result manifest → client polls/fetches → optional merge into one `.gpkg` (the multi-run query store already supported). Spot/preemptible instances are safe: runs are idempotent, and hotstart checkpointing makes even long runs resumable.

### 6.2 RL rollouts (gymnasium)

**Anti-pattern (rejected): remote env, local policy.** `env.step()` over WAN = 10–100 ms per step against a sub-ms solver step — a 100–1000× slowdown. This rules out any "env-as-a-service stepped over the network" design for training.

**Pattern (recommended): ship the rollout, not the step.** Worker containers run env *and* policy inference together (Ray RLlib rollout workers, or plain workers receiving policy weights periodically). Per-episode artifacts (JSONL trajectories via the existing `RecordTrajectory` wrapper, plus `.out` on demand) upload to blob storage; only weights and aggregated metrics cross the network. `AsyncVectorEnv` keeps working *inside* each worker for per-node parallelism — the existing design composes cleanly.

### 6.3 Interactive MCP sessions

The MCP server stays where it is (local, stdio with the LLM client) and gains a **remote session backend**: instead of constructing an in-process `Solver`, the session proxies lifecycle/forcing/query tools over HTTP/gRPC to a session-server container (the same image in session mode) holding the live handle. Requirements: sticky routing (session ID → pod; a K8s StatefulSet or simple registry suffices at this scale — interactive sessions number in the tens, not thousands), idle timeout with `hotstart_save` checkpoint to blob storage, and resume-by-restore for migration or crash recovery. The existing `SessionManager` abstraction is the natural seam: today's backend duality (v6/legacy) shows the pattern; "remote" becomes a third backend.

---

## 7. Recommendation Summary

| Concern | Choice | Why |
|---|---|---|
| Portable contract | Run bundle + result manifest (JSON + files, content-addressed) | Makes every backend interchangeable; cheapest abstraction to test |
| Primary control plane | **Kubernetes + Kueue** (EKS/AKS/GKE/k3s) | Only option covering all four targets and all three workloads with one API |
| RL execution | **Ray via KubeRay** on the same clusters | Rollout-worker model is exactly the right offload unit |
| HPC batch | SLURM `Executor` backend (Apptainer image) | Near-free to add atop the bundle contract |
| Serverless / managed batch | Deferred; possible later `Executor` backends | API fragmentation, runtime limits, no on-prem |
| Data plane | **Blob storage via `fsspec`** (S3/Blob/GCS/MinIO/local) | One code path everywhere incl. on-prem and dev |
| Interactive transport | HTTP/gRPC session server, hotstart checkpoint/restore | Only stateful piece; checkpoints make it disposable |
| Burst from local | Local subprocess `Executor` is default; spill to cloud `Executor` when queue depth/ETA exceeds threshold | Same interface, policy decides placement |

Cross-cutting: one OCI image (pinned engine version recorded in every result manifest — reproducibility), short-lived per-job credentials via presigned URLs or workload identity, compute scheduled in the same region as its bucket to avoid egress.

---

## 8. Roadmap

Per project convention, every phase's definition of done includes documented `.pyi` stubs and unit tests against the **real handle-based `Solver` — no engine mocks**; test files written to user-reviewable locations, not temp dirs. Each phase is independently shippable.

**Phase 1 — Run bundle + result manifest format.**
New module (proposed: `openswmm_mcp.offload.bundle`): build/hash/pack/unpack bundles, write/validate manifests.
*DoD:* `.pyi` stubs; tests that pack a real benchmark `.inp` + hotstart, unpack, run the real `Solver` end-to-end from the unpacked bundle, and validate the result manifest against the produced `.out`/`.rpt`.

**Phase 2 — `Executor` protocol + local backend.**
`Executor` protocol (`submit(bundle) → job_id`, `status(job_id)`, `fetch(job_id) → results`, `cancel`); `LocalExecutor` via subprocess pool. This alone delivers parallel local sweeps and is the burst-from-local foundation.
*DoD:* stubs; tests running ≥4 concurrent real-Solver sweep members locally, verifying isolation and manifest correctness.

**Phase 3 — Data plane via `fsspec`.**
Bundle/result store over `fsspec` URLs; conformance test suite run against `file://` and MinIO (containerized in CI); content-address dedup.
*DoD:* stubs; conformance tests upload/download a real run's artifacts through MinIO with hash verification.

**Phase 4 — Simulation container image.**
One Dockerfile: engine + runner with `batch` and `session-server` modes; version stamping into manifests. Apptainer conversion documented for SLURM.
*DoD:* image runs a Phase-1 bundle to completion in plain Docker; session mode answers a lifecycle smoke sequence (open → step ×N → hotstart_save → close) over HTTP.

**Phase 5 — `KubernetesExecutor` (Jobs + Kueue).**
Same protocol as Phase 2; manifests templated, no provider-specific fields; tested against a local kind/k3d cluster.
*DoD:* stubs; integration test submits a 10-member sweep to kind, fetches all manifests via the Phase-3 store.

**Phase 6 — MCP integration.**
New tool namespace (`jobs_submit_sweep`, `jobs_status`, `jobs_fetch_results`, `jobs_cancel`) over the `Executor`; remote session backend in `SessionManager` proxying to a session-server with hotstart checkpoint/resume.
*DoD:* stubs; tests exercise submit→fetch through MCP tools against `LocalExecutor`; remote-session test does checkpoint→kill→restore against a real session-server container.

**Phase 7 — Gymnasium rollout offload.**
Rollout-worker entrypoint (env + policy colocated, trajectories to blob store); KubeRay deployment recipe; explicit non-goal: per-step remote env.
*DoD:* stubs; test runs a real-Solver TwinTank rollout in a worker container and round-trips the JSONL trajectory through the store.

**Phase 8 — Burst policy + provider profiles.**
`RoutingExecutor` wrapping local + remote with a placement policy (queue depth / ETA threshold); documented cluster profiles for EKS, AKS, GKE, k3s, SLURM.
*DoD:* stubs; test forces saturation of `LocalExecutor` and asserts spill-over routing (remote backend exercised via the kind cluster from Phase 5).

---

## 9. Open Questions

1. **Where do shared pieces live?** Bundle/Executor code is useful to both repos — third package (`openswmm.offload`) vs. living in `openswmm.mcp` with gymnasium depending on it. Leaning third package; decide before Phase 1.
2. **Linux engine wheels.** Containers are Linux; current builds are macOS-only in the sandbox. Phase 4 is blocked until Linux wheels (or in-container source builds) exist — this is the single biggest practical prerequisite.
3. **Result retention policy.** Thousands-scale sweeps produce large `.out` volumes; default lifecycle rule (e.g., 30-day expiry, manifests kept) to be agreed.
4. **Secrets model per environment.** Workload identity (cloud K8s) vs. presigned URLs (SLURM/local) — likely both, chosen per backend.
